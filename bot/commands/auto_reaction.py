from time import sleep

import emoji as emoji
from django.template.loader import get_template
from telegram import CallbackQuery, ReplyKeyboardMarkup
from telegram.ext import CallbackQueryHandler, MessageHandler
from telegram.error import TimedOut, RetryAfter, BadRequest

from bot.commands import BaseCommand
from bot.commands.auto_edit import AutoEdit
from bot.filters import Filters as OwnFilters
from bot.models.channel_settings import ChannelSettings
from bot.models.reactions import Reaction
from bot.models.usersettings import UserSettings
from bot.utils.chat import build_menu, channel_selector_menu


class AutoReaction(AutoEdit):
    BaseCommand.register_start_button('Reactions')

    @BaseCommand.command_wrapper(CallbackQueryHandler, pattern='^reaction:.*')
    def update_reaction(self):
        query: CallbackQuery = self.update.callback_query
        data = query.data
        _, message_id, emoji = data.split(':')
        try:
            reactions = Reaction.objects.filter(message=message_id, channel=self.channel_settings,
                                                bot_token=self.bot.token)

            clicked = reactions.get(reaction=emoji)
        except Exception:
            query.answer('Sorry, something went wrong.')
            return

        already_clicked = False
        if clicked.users.filter(pk=self.user_settings.pk).exists():
            already_clicked = True

        for reaction in reactions.filter(users=self.user_settings).all():
            reaction.users.remove(self.user_settings)
            reaction.save()

        try:
            if not already_clicked:
                clicked.users.add(self.user_settings)
                clicked.save()
                query.answer(f'You reacted with {emoji}')
            else:
                query.answer(f'You took your reaction {emoji} back')
        except BadRequest:
            pass

        while True:
            try:
                self.message.edit_reply_markup(reply_markup=self.new_reply_buttons(), timeout=60).result()
            except TimedOut:
                continue
            except RetryAfter as e:
                sleep(e.retry_after)
                continue
            except BadRequest:
                pass
            break

    @BaseCommand.command_wrapper(MessageHandler,
                                 filters=OwnFilters.text_is('Reactions') & OwnFilters.state_is(UserSettings.IDLE))
    def caption_menu(self):
        menu = channel_selector_menu(self.user_settings, 'change_reactions')
        message = get_template('commands/auto_reactions/main.html').render()

        if not menu:
            self.message.reply_text(message)
            self.message.reply_text('No channels added yet.')
            return

        self.user_settings.state = UserSettings.SET_REACTIONS_MENU
        self.message.reply_html(message, reply_markup=ReplyKeyboardMarkup([['Cancel']]))
        self.message.reply_text('Channels:', reply_markup=menu)

    @BaseCommand.command_wrapper(CallbackQueryHandler, pattern='^change_reactions:.*$')
    def pre_set_reaction(self):
        channel_id = int(self.update.callback_query.data.split(':')[1])
        member = self.bot.get_chat_member(chat_id=channel_id, user_id=self.user.id)

        if not member.can_change_info and not member.status == member.CREATOR:
            self.message.reply_text('You must have change channel info permissions to change the reactions.')
            return

        self.user_settings.current_channel = ChannelSettings.objects.get(channel_id=channel_id,
                                                                         bot_token=self.bot.token)
        self.user_settings.state = UserSettings.SET_REACTIONS

        self.update.callback_query.answer()
        self.message.delete()

        reactions = self.user_settings.current_channel.reactions
        reaction_str = None
        if reactions:
            reaction_str = ', '.join(reactions)

        message = get_template('commands/auto_reactions/new.html').render({
            'channel_name': self.user_settings.current_channel.link,
            'current_reactions': reaction_str,
        })

        self.message.reply_html(message, reply_markup=ReplyKeyboardMarkup(build_menu('Clear', 'Cancel')),
                                disable_web_page_preview=True)

    @BaseCommand.command_wrapper(MessageHandler, filters=OwnFilters.state_is(UserSettings.SET_REACTIONS))
    def set_reactions(self):
        text = self.message.text_markdown

        if not text:
            self.message.reply_text('You have to send me some emojis.')
            return
        elif text in ['Cancel', 'Home']:
            return
        elif text == 'Clear':
            reactions = None
        else:
            emojis = emoji.emoji_lis(self.message.text)
            reactions = [reaction['emoji'] for reaction in emojis]

        if reactions is None:
            self.user_settings.current_channel.reactions = None
            message = f'Reactions for {self.user_settings.current_channel.name} cleared'
        elif not reactions:
            message = f'No reactions given. You have to give me emojis as reactions.'
        else:
            message = f'Reactions of {self.user_settings.current_channel.name} were set to:\n{", ".join(reactions)}'
            self.user_settings.current_channel.reactions = reactions
        self.user_settings.current_channel.save()

        self.message.reply_markdown(message, reply_markup=ReplyKeyboardMarkup([['Clear', 'Home']]))

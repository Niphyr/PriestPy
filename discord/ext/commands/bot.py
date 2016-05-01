# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2015-2016 Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

import asyncio
import discord
import inspect
import importlib
import sys

from .core import GroupMixin, Command, command
from .view import StringView
from .context import Context
from .errors import CommandNotFound
from .formatter import HelpFormatter

def _get_variable(name):
    stack = inspect.stack()
    try:
        for frames in stack:
            current_locals = frames[0].f_locals
            if name in current_locals:
                return current_locals[name]
    finally:
        del stack

def when_mentioned(bot, msg):
    """A callable that implements a command prefix equivalent
    to being mentioned, e.g. ``@bot ``."""
    server = msg.server
    if server is not None:
        return '{0.me.mention} '.format(server)
    return '{0.user.mention} '.format(bot)

def when_mentioned_or(*prefixes):
    """A callable that implements when mentioned or other prefixes provided.

    Examples
    ---------

    .. code-block:: python

        bot = commands.Bot(command_prefix=commands.when_mentioned_or('!'))

    See Also
    ----------
    :func:`when_mentioned`
    """
    def inner(bot, msg):
        r = list(prefixes)
        r.append(when_mentioned(bot, msg))
        return r

    return inner

@asyncio.coroutine
def _default_help_command(ctx, *commands : str):
    """Shows this message."""
    bot = ctx.bot
    destination = ctx.message.author if bot.pm_help else ctx.message.channel

    # help by itself just lists our own commands.
    if len(commands) == 0:
        pages = bot.formatter.format_help_for(ctx, bot)
    elif len(commands) == 1:
        # try to see if it is a cog name
        name = commands[0]
        command = None
        if name in bot.cogs:
            command = bot.cogs[name]
        else:
            command = bot.commands.get(name)
            if command is None:
                yield from bot.send_message(destination, bot.command_not_found.format(name))
                return

        pages = bot.formatter.format_help_for(ctx, command)
    else:
        name = commands[0]
        command = bot.commands.get(name)
        if command is None:
            yield from bot.send_message(destination, bot.command_not_found.format(name))
            return

        for key in commands[1:]:
            try:
                command = command.commands.get(key)
                if command is None:
                    yield from bot.send_message(destination, bot.command_not_found.format(key))
                    return
            except AttributeError:
                yield from bot.send_message(destination, bot.command_has_no_subcommands.format(command, key))
                return

        pages = bot.formatter.format_help_for(ctx, command)

    if bot.pm_help is None:
        characters = sum(map(lambda l: len(l), pages))
        # modify destination based on length of pages.
        if characters > 1000:
            destination = ctx.message.author

    for page in pages:
        yield from bot.send_message(destination, page)


class Bot(GroupMixin, discord.Client):
    """Represents a discord bot.

    This class is a subclass of :class:`discord.Client` and as a result
    anything that you can do with a :class:`discord.Client` you can do with
    this bot.

    This class also subclasses :class:`GroupMixin` to provide the functionality
    to manage commands.

    Attributes
    -----------
    command_prefix
        The command prefix is what the message content must contain initially
        to have a command invoked. This prefix could either be a string to
        indicate what the prefix should be, or a callable that takes in the bot
        as its first parameter and :class:`discord.Message` as its second
        parameter and returns the prefix. This is to facilitate "dynamic"
        command prefixes.

        The command prefix could also be a list or a tuple indicating that
        multiple checks for the prefix should be used and the first one to
        match will be the invocation prefix. You can get this prefix via
        :attr:`Context.prefix`.
    description : str
        The content prefixed into the default help message.
    formatter : :class:`HelpFormatter`
        The formatter used to format the help message. By default, it uses a
        the :class:`HelpFormatter`. Check it for more info on how to override it.
        If you want to change the help command completely (add aliases, etc) then
        a call to :meth:`remove_command` with 'help' as the argument would do the
        trick.
    pm_help : Optional[bool]
        A tribool that indicates if the help command should PM the user instead of
        sending it to the channel it received it from. If the boolean is set to
        ``True``, then all help output is PM'd. If ``False``, none of the help
        output is PM'd. If ``None``, then the bot will only PM when the help
        message becomes too long (dictated by more than 1000 characters).
        Defaults to ``False``.
    help_attrs : dict
        A dictionary of options to pass in for the construction of the help command.
        This allows you to change the command behaviour without actually changing
        the implementation of the command. The attributes will be the same as the
        ones passed in the :class:`Command` constructor. Note that ``pass_context``
        will always be set to ``True`` regardless of what you pass in.
    command_not_found : str
        The format string used when the help command is invoked with a command that
        is not found. Useful for i18n. Defaults to ``"No command called {} found."``.
        The only format argument is the name of the command passed.
    command_has_no_subcommands : str
        The format string used when the help command is invoked with requests for a
        subcommand but the command does not have any subcommands. Defaults to
        ``"Command {0.name} has no subcommands."``. The first format argument is the
        :class:`Command` attempted to get a subcommand and the second is the name.
    """
    def __init__(self, command_prefix, formatter=None, description=None, pm_help=False, **options):
        super().__init__(**options)
        self.command_prefix = command_prefix
        self.extra_events = {}
        self.cogs = {}
        self.extensions = {}
        self.description = inspect.cleandoc(description) if description else ''
        self.pm_help = pm_help
        self.command_not_found = options.pop('command_not_found', 'No command called "{}" found.')
        self.command_has_no_subcommands = options.pop('command_has_no_subcommands', 'Command {0.name} has no subcommands.')

        self.help_attrs = options.pop('help_attrs', {})
        self.help_attrs['pass_context'] = True

        if 'name' not in self.help_attrs:
            self.help_attrs['name'] = 'help'

        if formatter is not None:
            if not isinstance(formatter, HelpFormatter):
                raise discord.ClientException('Formatter must be a subclass of HelpFormatter')
            self.formatter = formatter
        else:
            self.formatter = HelpFormatter()

        # pay no mind to this ugliness.
        self.command(**self.help_attrs)(_default_help_command)

    # internal helpers

    def _get_prefix(self, message):
        prefix = self.command_prefix
        if callable(prefix):
            return prefix(self, message)
        else:
            return prefix

    @asyncio.coroutine
    def _run_extra(self, coro, event_name, *args, **kwargs):
        try:
            yield from coro(*args, **kwargs)
        except asyncio.CancelledError:
            pass
        except Exception:
            try:
                yield from self.on_error(event_name, *args, **kwargs)
            except asyncio.CancelledError:
                pass

    def dispatch(self, event_name, *args, **kwargs):
        super().dispatch(event_name, *args, **kwargs)
        ev = 'on_' + event_name
        if ev in self.extra_events:
            for event in self.extra_events[ev]:
                coro = self._run_extra(event, event_name, *args, **kwargs)
                discord.compat.create_task(coro, loop=self.loop)

    # utility "send_*" functions

    def say(self, *args, **kwargs):
        """|coro|

        A helper function that is equivalent to doing

        .. code-block:: python

            self.send_message(message.channel, *args, **kwargs)

        See Also
        ---------
        :meth:`Client.send_message`
        """
        destination = _get_variable('_internal_channel')
        return self.send_message(destination, *args, **kwargs)

    def whisper(self, *args, **kwargs):
        """|coro|

        A helper function that is equivalent to doing

        .. code-block:: python

            self.send_message(message.author, *args, **kwargs)

        See Also
        ---------
        :meth:`Client.send_message`
        """
        destination = _get_variable('_internal_author')
        return self.send_message(destination, *args, **kwargs)

    def reply(self, content, *args, **kwargs):
        """|coro|

        A helper function that is equivalent to doing

        .. code-block:: python

            msg = '{0.mention}, {1}'.format(message.author, content)
            self.send_message(message.channel, msg, *args, **kwargs)

        See Also
        ---------
        :meth:`Client.send_message`
        """
        author = _get_variable('_internal_author')
        destination = _get_variable('_internal_channel')
        fmt = '{0.mention}, {1}'.format(author, str(content))
        return self.send_message(destination, fmt, *args, **kwargs)

    def upload(self, *args, **kwargs):
        """|coro|

        A helper function that is equivalent to doing

        .. code-block:: python

            self.send_file(message.channel, *args, **kwargs)

        See Also
        ---------
        :meth:`Client.send_file`
        """
        destination = _get_variable('_internal_channel')
        return self.send_file(destination, *args, **kwargs)

    def type(self):
        """|coro|

        A helper function that is equivalent to doing

        .. code-block:: python

            self.send_typing(message.channel)

        See Also
        ---------
        The :meth:`Client.send_typing` function.
        """
        destination = _get_variable('_internal_channel')
        return self.send_typing(destination)

    # listener registration

    def add_listener(self, func, name=None):
        """The non decorator alternative to :meth:`listen`.

        Parameters
        -----------
        func : coroutine
            The extra event to listen to.
        name : Optional[str]
            The name of the command to use. Defaults to ``func.__name__``.

        Examples
        ---------

        .. code-block:: python

            async def on_ready(): pass
            async def my_message(message): pass

            bot.add_listener(on_ready)
            bot.add_listener(my_message, 'on_message')

        """
        name = func.__name__ if name is None else name

        if not asyncio.iscoroutinefunction(func):
            raise discord.ClientException('Listeners must be coroutines')

        if name in self.extra_events:
            self.extra_events[name].append(func)
        else:
            self.extra_events[name] = [func]

    def remove_listener(self, func, name=None):
        """Removes a listener from the pool of listeners.

        Parameters
        -----------
        func
            The function that was used as a listener to remove.
        name
            The name of the event we want to remove. Defaults to
            ``func.__name__``.
        """

        name = func.__name__ if name is None else name

        if name in self.extra_events:
            try:
                self.extra_events[name].remove(func)
            except ValueError:
                pass

    def listen(self, name=None):
        """A decorator that registers another function as an external
        event listener. Basically this allows you to listen to multiple
        events from different places e.g. such as :func:`discord.on_ready`

        The functions being listened to must be a coroutine.

        Examples
        ---------

        .. code-block:: python

            @bot.listen
            async def on_message(message):
                print('one')

            # in some other file...

            @bot.listen('on_message')
            async def my_message(message):
                print('two')

        Would print one and two in an unspecified order.

        Raises
        -------
        discord.ClientException
            The function being listened to is not a coroutine.
        """

        def decorator(func):
            self.add_listener(func, name)
            return func

        return decorator

    # cogs

    def add_cog(self, cog):
        """Adds a "cog" to the bot.

        A cog is a class that has its own event listeners and commands.

        They are meant as a way to organize multiple relevant commands
        into a singular class that shares some state or no state at all.

        More information will be documented soon.

        Parameters
        -----------
        cog
            The cog to register to the bot.
        """

        self.cogs[type(cog).__name__] = cog
        members = inspect.getmembers(cog)
        for name, member in members:
            # register commands the cog has
            if isinstance(member, Command):
                member.instance = cog
                if member.parent is None:
                    self.add_command(member)
                continue

            # register event listeners the cog has
            if name.startswith('on_'):
                self.add_listener(member)

    def get_cog(self, name):
        """Gets the cog instance requested.

        If the cog is not found, ``None`` is returned instead.

        Parameters
        -----------
        name : str
            The name of the cog you are requesting.
        """
        return self.cogs.get(name)

    def remove_cog(self, name):
        """Removes a cog the bot.

        All registered commands and event listeners that the
        cog has registered will be removed as well.

        If no cog is found then ``None`` is returned, otherwise
        the cog instance that is being removed is returned.

        If the cog defines a special member function named ``__unload``
        then it is called when removal has completed. This function
        **cannot** be a coroutine. It must be a regular function.

        Parameters
        -----------
        name : str
            The name of the cog to remove.
        """

        cog = self.cogs.pop(name, None)
        if cog is None:
            return cog

        members = inspect.getmembers(cog)
        for name, member in members:
            # remove commands the cog has
            if isinstance(member, Command):
                member.instance = None
                if member.parent is None:
                    self.remove_command(member.name)
                continue

            # remove event listeners the cog has
            if name.startswith('on_'):
                self.remove_listener(member)

        unloader_name = '_{0.__class__.__name__}__unload'.format(cog)
        try:
            getattr(cog, unloader_name)()
        except AttributeError:
            pass

        del cog

    # extensions

    def load_extension(self, name):
        if name in self.extensions:
            return

        lib = importlib.import_module(name)
        if not hasattr(lib, 'setup'):
            raise discord.ClientException('extension does not have a setup function')

        lib.setup(self)
        self.extensions[name] = lib

    def unload_extension(self, name):
        lib = self.extensions.get(name)
        if lib is None:
            return

        # find all references to the module

        # remove the cogs registered from the module
        for cogname, cog in self.cogs.copy().items():
            if inspect.getmodule(cog) is lib:
                self.remove_cog(cogname)

        # first remove all the commands from the module
        for command in self.commands.copy().values():
            if command.module is lib:
                command.module = None
                if isinstance(command, GroupMixin):
                    command.recursively_remove_all_commands()
                self.remove_command(command.name)

        # then remove all the listeners from the module
        for event_list in self.extra_events.copy().values():
            remove = []
            for index, event in enumerate(event_list):
                if inspect.getmodule(event) is lib:
                    remove.append(index)

            for index in reversed(remove):
                del event_list[index]

        # finally remove the import..
        del lib
        del self.extensions[name]
        del sys.modules[name]

    # command processing

    @asyncio.coroutine
    def process_commands(self, message):
        """|coro|

        This function processes the commands that have been registered
        to the bot and other groups. Without this coroutine, none of the
        commands will be triggered.

        By default, this coroutine is called inside the :func:`on_message`
        event. If you choose to override the :func:`on_message` event, then
        you should invoke this coroutine as well.

        Warning
        --------
        This function is necessary for :meth:`say`, :meth:`whisper`,
        :meth:`type`, :meth:`reply`, and :meth:`upload` to work due to the
        way they are written. It is also required for the :func:`on_command`
        and :func:`on_command_completion` events.

        Parameters
        -----------
        message : discord.Message
            The message to process commands for.
        """
        _internal_channel = message.channel
        _internal_author = message.author

        view = StringView(message.content)
        if message.author == self.user:
            return

        prefix = self._get_prefix(message)
        invoked_prefix = prefix

        if not isinstance(prefix, (tuple, list)):
            if not view.skip_string(prefix):
                return
        else:
            invoked_prefix = discord.utils.find(view.skip_string, prefix)
            if invoked_prefix is None:
                return


        invoker = view.get_word()
        tmp = {
            'bot': self,
            'invoked_with': invoker,
            'message': message,
            'view': view,
            'prefix': invoked_prefix
        }
        ctx = Context(**tmp)
        del tmp

        if invoker in self.commands:
            command = self.commands[invoker]
            self.dispatch('command', command, ctx)
            ctx.command = command
            yield from command.invoke(ctx)
            self.dispatch('command_completion', command, ctx)
        else:
            exc = CommandNotFound('Command "{}" is not found'.format(invoker))
            self.dispatch('command_error', exc, ctx)

    @asyncio.coroutine
    def on_message(self, message):
        yield from self.process_commands(message)

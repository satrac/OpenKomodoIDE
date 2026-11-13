# Copyright (c) 2000-2006 ActiveState Software Inc.
# See the file LICENSE.txt for licensing information.

"""puttylib -- some utilities for working with PuTTY.

Currently this is very limited -- just enough to extract some PuTTY settings.
"""

import urllib.request, urllib.parse, urllib.error
import logging
try:
    import winreg
except ImportError:
    pass


#---- globals

log = logging.getLogger("puttylib")


#---- public routines

class PuTTYServer(object):
    alias = None
    hostname = None
    username = None
    port = None

    @classmethod
    def default_username(cls):
        if cls._default_username_cache is None:
            import getpass
            cls._default_username_cache = getpass.getuser()
        return cls._default_username_cache
    _default_username_cache = None

    def __init__(self, alias, hostname, port, username=None):
        self.alias = alias
        self.hostname = hostname
        self.port = port
        self.username = username or self.default_username()

    def __str__(self):
        return "%(alias)s=%(username)s@%(hostname)s:%(port)d" % self.__dict__


def server_from_registry(session_name, session_key):
    try:
        alias = urllib.parse.unquote(session_name)
        protocol, type = winreg.QueryValueEx(session_key, "Protocol")
        if protocol != "ssh":
            return None
        hostname, type = winreg.QueryValueEx(session_key, "HostName")
        username, type = winreg.QueryValueEx(session_key, "UserName")
        if not username:
            username = None
        port, type = winreg.QueryValueEx(session_key, "PortNumber")
        return PuTTYServer(alias, hostname, port, username=username)
    except EnvironmentError as ex:
        log.warning("error extracting server info for '%s' PuTTY session: %s",
                 session_name, ex)
        return None


def gen_putty_servers():
    """Generate the list of configured putty servers for the current user,
    if any.
    
    Yields PuTTYServer instances.
    """
    key_name = r"Software\SimonTatham\PuTTY\Sessions"

    try:
        sessions_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name)
    except EnvironmentError:
        pass
    else:
        index = -1
        while 1:
            index += 1
            try:
                session_name = winreg.EnumKey(sessions_key, index)
                if urllib.parse.unquote(session_name) == "Default Settings":
                    continue
                try:
                    session_key = winreg.OpenKey(sessions_key, session_name)
                except EnvironmentError:
                    pass
                else:
                    server = server_from_registry(session_name, session_key)
                    if server is not None:
                        yield server
            except EnvironmentError:
                break



if __name__ == "__main__":
    logging.basicConfig()
    for server in gen_putty_servers():
        print(server)

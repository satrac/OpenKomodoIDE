from .wkutils import Command


# Modified by ActiveState to add 'ignoreCache' option.
def reload(ignoreCache=False):
    params = {}
    params['ignoreCache'] = ignoreCache
    command = Command('Page.reload', params)
    return command

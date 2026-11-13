# Copyright 2017 ActiveState, Inc. All rights reserved.

"""Helper methods for querying information from the database.
The information returned is transformed into a non-database-specific format
(e.g. queries for symbols in files are returned as AbstractSymbol types, and not
database model Symbol types).
"""

import logging
import os
import operator

from abc import ABCMeta

from peewee import PeeweeException

from symbols import AbstractSymbol
from db.model import File, Symbol
from functools import reduce

log = logging.getLogger("codeintel.db.model.helpers")
#log.setLevel(logging.DEBUG)

def fileExists(filename):
    """Returns whether or not the given filename exists in the database.
    @param filename String filename to look for.
    @usage fileExists(PYTHON2_STDLIB_FILE)
    @return True or False
    """
    return File.exists(filename)

def fetchSymbolInFile(filename, name, type=None, top_level=True):
    """Fetches from the database the Symbol with the parameters, and returns it
    as an AbstractSymbol.
    If either the file or Symbol does not exist, returns None.
    @param filename String filename to fetch the Symbol from.
    @param name String name of the top-level Symbol to fetch.
    @param type Optional AbstractSymbol type to restrict the fetched symbol to.
    @param top_level Optional flag that indicates whether or not to fetch a
                     top-level symbol.
                     The default value is True.
    @return AbstractSymbol
    @usage exports = fetchSymbolInFile(js_filename, "exports", AbstractStruct)
    """
    if not isinstance(filename, str):
        raise TypeError("filename must be a string (got '%s')" % filename.__class__.__name__)
    if not isinstance(name, str):
        raise TypeError("name must be a string (got '%s')" % name.__class__.__name__)
    if type is not None and (type.__class__ != ABCMeta or not issubclass(type, AbstractSymbol)):
        raise TypeError("type must be a class derived from AbstractSymbol or None (got '%s')" % type.__class__.__name__)
    filename = os.path.normcase(filename) # normalize
    log.debug("Searching database for symbol '%s' in file '%s'", name, filename)
    try:
        fileId = File.idFromPath(filename)
        if not fileId:
            return None

        query = Symbol.name == name
        query &= Symbol.file_id == fileId

        if type:
            query &= Symbol.symbol_type == Symbol.getSymbolType(type)
        if top_level:
            query &= Symbol.parent == None

        return Symbol.select(Symbol).where(query).get().toAbstractSymbol()

    except (Symbol.DoesNotExist):
        return None

def fetchSymbolsInFile(filename, type=None, exclude=None):
    """Fetches from the database all top-level Symbols in the given string
    filename with the given type, and returns them as a list of AbstractSymbols.
    If either the file or Symbols do not exist, returns an empty list.
    @param filename String filename to fetch Symbols from. This should be an
                    absolute path.
    @param type Optional AbstractSymbol type or list of AbstractSymbol types to
                restrict fetched symbols to.
    @param exclude Optional AbstractSymbol type or list of AbstractSymbol types
                to exclude from fetched symbols.
    @return list of AbstractSymbols
    @usage builtins = fetchSymbolsInFile(stdlib_file, exclude=AbstractModule)
    @usage importables = fetchSymbolInFile(stdlib_file, AbstractModule)
    """
    if not isinstance(filename, str):
        raise TypeError("filename must be a string (got '%s')" % filename.__class__.__name__)
    if type is not None:
        if not isinstance(type, (list, tuple)):
            if type.__class__ != ABCMeta or not issubclass(type, AbstractSymbol):
                raise TypeError("type must be derived from AbstractSymbol or None (got '%s')" % type.__class__.__name__)
        else:
            for t in type:
                if t.__class__ != ABCMeta or not issubclass(t, AbstractSymbol):
                    raise TypeError("type must be derived from AbstractSymbol or None (got '%s')" % type.__class__.__name__)
    if exclude is not None:
        if not isinstance(exclude, (list, tuple)):
            if exclude.__class__ != ABCMeta or not issubclass(exclude, AbstractSymbol):
                raise TypeError("exclude must be derived from AbstractSymbol or None (got '%s')" % exclude.__class__.__name__)
        else:
            for t in exclude:
                if t.__class__ != ABCMeta or not issubclass(t, AbstractSymbol):
                    raise TypeError("exclude must be derived from AbstractSymbol or None (got '%s')" % exclude.__class__.__name__)
    filename = os.path.normcase(filename) # normalized
    log.debug("Searching database for symbols in filename '%s'", filename)
    try:
        fileId = File.idFromPath(filename)
        if not fileId:
            return []

        query = Symbol.file_id == fileId
        if type:
            if not isinstance(type, (list, tuple)):
                query &= Symbol.symbol_type == Symbol.getSymbolType(type)
            else:
                sub_query = Symbol.symbol_type == Symbol.getSymbolType(type[0])
                for t in type[1:]:
                    sub_query |= Symbol.symbol_type == Symbol.getSymbolType(t)
                query &= sub_query
        if exclude:
            if not isinstance(exclude, (list, tuple)):
                query &= Symbol.symbol_type != Symbol.getSymbolType(exclude)
            else:
                sub_query = Symbol.symbol_type != Symbol.getSymbolType(exclude[0])
                for t in exclude[1:]:
                    sub_query &= Symbol.symbol_type != Symbol.getSymbolType(t)
                query &= sub_query
        query &= Symbol.parent == None
        return [symbol.toAbstractSymbol() for symbol in Symbol.select(Symbol).where(query).iterator()]
    except (Symbol.DoesNotExist):
        return []

def fetchSymbolsInDirectoriesMatchingPath(dirnames, pathsuffix, name, type=None, limit=None):
    """Fetches from the database all top-level Symbols in the given string
    directories with the given suffix, name, and type, and returns them as a
    list of AbstractSymbols.
    If either the directories or Symbols do not exist, returns an empty list.
    @param dirnames String of dirname or list of string dirnames to fetch
                    filenames from. These should be absolute paths.
    @param pathsuffix String suffix that the path needs to match
    @param name Optional string name of the top-level Symbol to fetch.
    @param type Optional AbstractSymbol type to restrict fetched symbols to.
    @param ext Optional string file extension to search for Symbols in.
    @return list of AbstractSymbol
    """
    if isinstance(dirnames, str):
        dirnames = [dirnames]
    if not isinstance(dirnames, list):
        raise TypeError("dirnames must be a list or a string (got '%s')" % dirnames.__class__.__name__)
    if not isinstance(name, str):
        raise TypeError("name must be a string (got '%s')" % name.__class__.__name__)
    if type is not None and (type.__class__ != ABCMeta or not issubclass(type, AbstractSymbol)):
        raise TypeError("type must be a class derived from AbstractSymbol or None (got '%s')" % type.__class__.__name__)
    log.debug("Searching database for symbols named '%s' in directory '%r' with suffix '%s'", name, dirnames, pathsuffix)
    try:
        clauses = []
        for dirname in dirnames:
            dirname = os.path.normcase(dirname) # normalized
            if not dirname.endswith(os.path.sep):
                # Ensure a directory search and not partial-filename search.
                dirname += os.path.sep
            clauses.append(File.path.startswith(dirname))
        query = (reduce(operator.or_, clauses))
        query &= File.path.endswith(os.path.normcase(pathsuffix)) # normalized
        query &= Symbol.name == name
        if type:
            query &= Symbol.symbol_type == Symbol.getSymbolType(type)
        query &= Symbol.parent == None
        if limit:
            symbolSelect = Symbol.select(Symbol, File).join(File).where(query).limit(limit)
        else:
            symbolSelect = Symbol.select(Symbol, File).join(File).where(query)
        return [symbol.toAbstractSymbol() for symbol in symbolSelect.iterator()]
    except (File.DoesNotExist, Symbol.DoesNotExist):
        return []

def fetchSymbolsInDirectories(dirnames, name, type=None, ext=None):
    """Fetches from the database all top-level Symbols in the given string
    directories with the given name and type, and returns them as a list of
    AbstractSymbols.
    If either the directories or Symbols do not exist, returns an empty list.
    @param dirname String dirname or list of string dirnames to fetch filenames
                   from. These should be absolute paths.
    @param name Optional string name of the top-level Symbol to fetch. If it
                ends with * it will use this as a name prefix.
    @param type Optional AbstractSymbol type to restrict fetched symbols to.
    @param ext Optional string file extension to search for Symbols in.
    @return list of AbstractSymbol
    @usage namespaces = fetchSymbolsInDirectories("/project", "Foo",
                                                  AbstractNamespace, ".php")
    """
    if isinstance(dirnames, str):
        dirnames = [dirnames]
    if not isinstance(dirnames, list):
        raise TypeError("dirnames must be a list or a string (got '%s')" % dirnames.__class__.__name__)
    if name is not None and not isinstance(name, str):
        raise TypeError("name must be a string (got '%s')" % name.__class__.__name__)
    if type is not None and (type.__class__ != ABCMeta or not issubclass(type, AbstractSymbol)):
        raise TypeError("type must be a class derived from AbstractSymbol or None (got '%s')" % type.__class__.__name__)
    if ext is not None:
        if not isinstance(ext, str):
            raise TypeError("ext must be a string (got '%s')" % ext.__class__.__name__)
    log.debug("Searching database for symbols named '%s' in directories '%r'", name, dirnames)
    try:
        clauses = []
        for dirname in dirnames:
            dirname = os.path.normcase(dirname) # normalized
            if not dirname.endswith(os.path.sep):
                # Ensure a directory search and not partial-filename search.
                dirname += os.path.sep
            clauses.append(File.path.startswith(dirname))
        if len(dirnames) == 0:
            clauses.append(File.path.startswith(""))
        query = (reduce(operator.or_, clauses))
        if ext:
            query &= File.path.endswith(ext)
        if name:
            if name.endswith("*"):
                query &= Symbol.name.startswith(name[0:-1])
            else:
                query &= Symbol.name == name
        if type:
            query &= Symbol.symbol_type == Symbol.getSymbolType(type)
        query &= Symbol.parent == None
        return [symbol.toAbstractSymbol() for symbol in Symbol.select(Symbol, File).join(File).where(query).iterator()]
    except (File.DoesNotExist, Symbol.DoesNotExist):
        return []

def fetchFilesInDirectory(dirname, ext=None, strip_ext=True, include_dirs=True):
    """Fetches from the database all filenames in the given string directory
    path with the given extension, and returns them as a list of string
    basenames.
    If no files were found, returns an empty list.
    @param dirname String dirname to fetch filenames from. This should be an
                   absolute path.
    @param ext Optional string or list of string extensions to limit fetched
               files to.
    @param strip_ext Optional flag that indicates whether or not to strip the
                     extension from returned filenames.
                     The default value is True.
    @param include_dirs Optional flag that indicates whether or not to include
                        directory names in the results. They will have a
                        trailing slash appended to them. The trailing slash is a
                        '/', regardless of platform, since most programming
                        languages use '/' instead of '\\'.
                        The default value is True.
    @return list of string file basenames
    """
    if not isinstance(dirname, str):
        raise TypeError("dirname must be a string (got '%s')" % dirname.__class__.__name__)
    if ext is not None:
        if not isinstance(ext, (str, list, tuple)):
            raise TypeError("ext must be a string or list (got '%s')" % ext.__class__.__name__)
    dirname = os.path.normcase(dirname) # normalized
    log.debug("Searching for files in directory '%s'", dirname)
    filenames = {}
    try:
        if not dirname.endswith(os.path.sep):
            # Ensure a directory search and not partial-filename search.
            dirname += os.path.sep
        db_files = File.select().where(File.path.startswith(dirname))
        for db_file in db_files.iterator():
            filename = db_file.path[len(dirname):] # strip dirname
            if filename.find(os.path.sep) == -1:
                # filename is a basename. Check its extension.
                if isinstance(ext, (list, tuple)):
                    for ext_ in ext:
                        if filename.endswith(ext_):
                            if strip_ext:
                                filename = filename[:-len(ext_)]
                            filenames[filename] = True
                            break
                elif not ext or filename.endswith(ext):
                    if ext and strip_ext:
                        filename = filename[:-len(ext)]
                    filenames[filename] = True
            elif include_dirs:
                parts = filename.split(os.path.sep)
                filename = parts[0] + '/' # use '/' instead of os.path.sep
                if filename not in filenames:
                    filenames[filename] = True
    except File.DoesNotExist:
        pass
    except PeeweeException:
        log.exception("DB Query Failed")
    return list(filenames.keys())

def fetchAllFilesInDirectory(dirname, ext=None):
    """Fetches from the database all filenames in the given string directory
    path with the given extension, and returns them as a list of string
    absolute filenames.
    If no files were found, returns an empty list.
    @param dirname String dirname to fetch filenames from. This should be an
        absolute path.
    @param ext Optional string or list of string extensions to limit fetched
        files to.
    @return list of string file absolute filenames
    """
    if not isinstance(dirname, str):
        raise TypeError("dirname must be a string (got '%s')" % dirname.__class__.__name__)
    if ext is not None:
        if not isinstance(ext, (str, list, tuple)):
            raise TypeError("ext must be a string or list (got '%s')" % ext.__class__.__name__)
    dirname = os.path.normcase(dirname) # normalized
    log.debug("Searching for files in directory '%s'", dirname)
    filenames = []
    try:
        if not dirname.endswith(os.path.sep):
            # Ensure a directory search and not partial-filename search.
            dirname += os.path.sep
        db_files = File.select().where(File.path.startswith(dirname))
        for db_file in db_files.iterator():
            filename = db_file.path
            if isinstance(ext, (list, tuple)):
                for ext_ in ext:
                    if filename.endswith(ext_):
                        filenames.append(filename)
                        break
            elif not ext or filename.endswith(ext):
                filenames.append(filename)
    except File.DoesNotExist:
        pass
    except PeeweeException:
        log.exception("DB Query Failed")
    return filenames

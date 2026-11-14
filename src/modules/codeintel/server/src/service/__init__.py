__all__ = ['ServiceSymbol', 'Service']

from language.router import Languages
from db.model.helpers import fetchSymbolsInFile
from symbols import AbstractScope, AbstractSymbol
from service.symbol import ServiceSymbol
from db.model.file import File as FileModel
from db.model.symbol import Symbol as SymbolModel
from scanner import Scanner
import os
import re
import fnmatch
import datetime
import threading

import logging
from functools import cmp_to_key

log = logging.getLogger("service")
log.setLevel(10)

class Service:

    def getLanguages(self):
        result = {}
        languages = Languages.getLanguages()

        for language in languages:
            result[language.name] = {
                "supports": language.supports,
                "extrapaths": language.extrapaths,
                "completion_prefixes": language.completion_prefixes,
                "completion_suffixes": language.completion_suffixes,
                "completion_word_characters": language.completion_word_characters,
                "completion_query_characters": language.completion_query_characters or language.completion_word_characters,
                "completion_trigger_blacklist": language.completion_trigger_blacklist,
                "completion_trigger_empty_lines": language.completion_trigger_empty_lines,
                "api": "codeintel"
            }

        return result

    def getCompletions(self, buf, pos, path, parentPath, importPaths, languageName, limit):
        log.debug("Getting completions for %s at pos %d" % (path, pos))

        if path:
            path = os.path.normcase(path)
        if parentPath:
            parentPath = os.path.normcase(parentPath)

        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            return

        # To take it easy on our API protocol we combine completions with
        # calltip information, as they are triggered at the same time in much
        # the same way

        env = self._getEnv(path, parentPath, importPaths, language)
        context = language.scanner.getCompletionContext(buf, pos, env)
        calltipContext = language.scanner.getCallTipContext(buf, pos, env)

        if not context:
            return

        completions = context.getCompletions()

        if not completions:
            return

        docblock = ""
        signature = ""
        if calltipContext:
            calltip = calltipContext.getCallTip()

            if calltip:
                docblock = calltip.summary
                signature = calltip.signature

            if docblock:
                docblock = docblock.strip()

        result = {
            "symbol": completions.symbol_name,
            "query": completions.name_part,
            "docblock": docblock,
            "signature": signature,
            "entries": [],
            "language": context.language
        }

        entries = []
        for k, completion in completions.members.items():
            symbol = ServiceSymbol(completion, k).toDict()
            entries.append(symbol)

        if completions.name_part:
            entries = self._weighed(entries, completions.name_part, path, languageName)
        else:
            entries = sorted(entries, key=lambda k: k['name'].lower())

        # Show cix entries and "private" properties last
        entries = sorted(entries, key=lambda k: k['source'] == "cix")
        entries = sorted(entries, key=lambda k: k['name'][0] == "_")

        result["entries"] = entries[:limit]

        return result

    def getDefinition_Async(self, buf, pos, path, parentPath, importPaths, languageName, callback=None):
        log.debug("Getting definition for %s at pos %d" % (path, pos))

        if path:
            path = os.path.normcase(path)
        if parentPath:
            parentPath = os.path.normcase(parentPath)

        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            if callback:
                callback(None, True)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            if callback:
                callback(None, True)
            return

        env = self._getEnv(path, parentPath, importPaths, language)
        context = language.scanner.getGotoDefinitionContext(buf, pos, env)

        if not context:
            if callback:
                callback(None, True)
            return

        definition = context.getDefinition()

        if not definition:
            if callback:
                callback(None, True)
            return

        parent = definition._symbol
        parents = []
        while (isinstance(parent, AbstractScope) and isinstance(parent.enclosingScope, AbstractSymbol)):
            parent = parent.enclosingScope
            parents.append(parent.name)

        parents.reverse()

        result = {
            "symbol": definition.name,
            "type": definition.type,
            "filename": definition.filename,
            "line": definition.line,
            "parents": parents
            #"language": context.language # TODO?
        }

        if callback:
            callback(result, True)
        return result

    def getReferences_Async(self, buf, pos, path, parentPath, languageName, callback = None):
        log.debug("Getting references for the symbol in %s at pos %d" % (path, pos))
        if path:
            path = os.path.normcase(path)
        if parentPath:
            parentPath = os.path.normcase(parentPath)

        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            if callback:
                callback(None, True)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            if callback:
                callback(None, True)
            return

        env = self._getEnv(path, parentPath, "", language)
        context = language.scanner.getFindReferencesContext(buf, pos, env)

        if not context:
            if callback:
                callback(None, True)
            return

        resultsAll = []
        def _callback(path, references):
            results = []
            for reference in sorted(references, key=lambda ref: ref.filename):
                result = {
                    "name": reference.symbol_name,
                    "path": reference.filename,
                    "lineData": reference.lineData,
                    "line": reference.line,
                    "pos": reference.pos
                }
                results.append(result)
                resultsAll.append(result)
            if callback:
                callback({ "path": path, "results": results }, False)

        context.getReferencesWithCb(_callback)

        if callback:
            callback(None, True)

        return resultsAll

    def getSymbolsInBuffer(self, buf, line, pos, indentString, languageName, sortType):
        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            return

        result = []

        scope = language.scanner.scan(buf)
        if not scope or not isinstance(scope, AbstractScope):
            return

        caretScope = None
        if line != -1:
            caretScope = scope.resolveScope(line)

            if not isinstance(caretScope, AbstractSymbol):
                caretScope = None

        queue = [[None, scope.members]]

        for entry in queue:
            parent, members = entry

            if parent is None:
                parent = result

            for name, member in members.items():

                idx = len(parent)

                symbol = ServiceSymbol(member, name).toDict()

                ctx = getattr(symbol, "ctx", {})
                if caretScope and symbol["line"] == getattr(ctx, "line", -1):
                    symbol["active"] = True

                parent.append(symbol)

                if isinstance(member, AbstractScope):
                    queue.append([parent[idx]["members"], member.members])

            if sortType == "organic":
                parent[:] = sorted(parent,
                                   key=lambda k: (k.get('line') is None,
                                                  k.get('line') or 0))
            else:
                parent[:] = sorted(parent,
                                   key=lambda k: (k.get('name') is None,
                                                  k.get('name') or ""))


        return result

    def getCaretScope(self, buf, line, pos, languageName):
        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            return

        scope = language.scanner.scan(buf)
        if not scope or not isinstance(scope, AbstractScope):
            return

        caretScope = scope.resolveScope(line)
        if not caretScope or not isinstance(caretScope, AbstractSymbol):
            return

        return ServiceSymbol(caretScope).toDict()

    def getNextScope(self, buf, line, languageName):
        language = Languages.getLanguage(languageName)
        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            return

        scope = language.scanner.scan(buf)
        if not scope or not isinstance(scope, AbstractScope):
            return

        caretScope = scope.resolveScope(line)
        if not caretScope:
            return

        while caretScope is not scope.enclosingScope:
            for index, symbol in enumerate(self._sortSymbols(caretScope.members)):
                if symbol.isScope and symbol.line > line:
                    return symbol.toDict()
            caretScope = caretScope.enclosingScope

    def getPrevScope(self, buf, line, languageName):
        language = Languages.getLanguage(languageName)
        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            return

        scope = language.scanner.scan(buf)
        if not scope or not isinstance(scope, AbstractScope):
            return

        caretScope = scope.resolveScope(line)
        if not caretScope:
            return

        while caretScope is not scope.enclosingScope:
            symbols = self._sortSymbols(caretScope.members)
            for index, symbol in enumerate(symbols):
                if symbol.isScope and symbol.line >= line and index > 0 and symbols[index - 1].line < line:
                    return symbols[index - 1].toDict()
            if len(symbols) > 0 and symbol.line < line:
                # Previous symbol is only symbol in parent scope.
                return symbols[index].toDict()
            caretScope = caretScope.enclosingScope

    def _sortSymbols(self, members, sortType="line"):
        """
        Returns a sorted array of ServiceSymbol objects
        sortType:
            line - sorts by location in file.
            name - sorts by symbol name
        """
        results = []
        for name, member in members.items():
            results.append(ServiceSymbol(member, name))
        if sortType == "line":
            results[:] = sorted(results, key=lambda k: k.line)
        elif sortType == "name":
            results[:] = sorted(results, key=lambda k: k.name)
        return results

    def getSymbols(self, searchQuery, path, parentPath, type, language, limit):
        log.debug("Getting symbols for %s" % path)

        path = os.path.normcase(path)
        parentPath = os.path.normcase(parentPath)

        if not searchQuery:
            searchQuery = ""

        queryWords = [_f for _f in re.split("\W+", searchQuery) if _f]
        queryWordsStr = "%".join(queryWords).replace("'","\\'")
        queryWordsStr = "%%%s%%" % queryWords

        query = SymbolModel.select(SymbolModel, FileModel).join(FileModel)

        if parentPath:
            query = query.where(FileModel.path.startswith(parentPath))

        if type:
            query = query.where(SymbolModel.symbol_type == type)

        for word in queryWords:
            query = query.where(SymbolModel.name.contains(word))

        if limit:
            query = query.limit(limit)

        result = []
        for symbol in query:
            symbol = ServiceSymbol(symbol).toDict()
            result.append(symbol)

        return self._weighed(result, searchQuery, path, language)

    def scanSummary_Async(self, paths, maxDepth, excludes, limit = 1000, callback = None):
        """Checks the given file or directory and returns all the files that
        codeintel would scan if called with scan_Async
        @param path List full paths to the files or directories to scan.
        @param maxDepth Optional maximum directory depth to scan. The default
                         value is 10.
        @param excludes Optional list of directories to exclude in the scan. The
                       default value is the empty list.
        @param limit maximum number of items
        """
        log.debug("Summarizing %s, depth: %d" % (", ".join(paths), maxDepth))
        self._scanSummary(paths, maxDepth, excludes, limit, callback)

    def _scanSummary(self, paths, maxDepth, excludes, limit = 1000, callback = None):
        result = { "paths": [], "count": 0 }

        def getOutdatedEntries(paths):
            unseen = paths
            results = []

            for f in FileModel.select().where((FileModel.path << paths)):
                modified = os.path.getmtime(f.path)
                modified = datetime.datetime.fromtimestamp(modified)

                if modified > f.created:
                    results.append(f.path)

                unseen.remove(f.path)

            results = results + unseen

            return results

        def isExcluded(path):
            path = path.replace("\\", "/")
            pathbits = path.split("/")
            for exclude in excludes:
                if os.sep in exclude:
                    if fnmatch.fnmatch(path, exclude):
                        return True
                if len(fnmatch.filter(pathbits, exclude)):
                    return True
            return False

        def walk(path):
            if os.path.isfile(path):
                yield os.path.dirname(path), [], [os.path.basename(path)]
                return
            for root, dirs, files in os.walk(path):
                depth = root[len(path) + len(os.path.sep):].count(os.path.sep)
                if depth > maxDepth:
                    break
                yield root, dirs, files

        # Walk the given paths and find file paths that we track
        subPaths = []
        for path in paths:
            path = os.path.normcase(path)
            for root, dirs, files in walk(path):
                for f in files:
                    fp = os.path.normcase(os.path.join(root, f))
                    ext = os.path.splitext(fp)[1][1:]
                    language = Languages.getLanguageFromExt(ext)
                    if language and not isExcluded(fp) and os.path.exists(fp):
                        subPaths.append(fp)

        # sqlite doesn't like it when we send it more than X variables, so
        # we need to segment the paths into smaller lists
        size = 100
        _subPaths = []
        while len(subPaths) > size or len(subPaths):
            _subPaths.append(subPaths[:size])
            subPaths = subPaths[size:]

        subPaths = []
        for subPathsSegmented in _subPaths:
            subPaths = subPaths + getOutdatedEntries(subPathsSegmented)

        for path in subPaths:
            ext = os.path.splitext(path)[1][1:]
            language = Languages.getLanguageFromExt(ext)
            if language and language.scanner:
                result["count"] = result["count"] + 1
                result["paths"].append((path, language.name))

        if limit and len(result["paths"]) > limit:
            result["paths"] = result["paths"][0:limit]

        if callback:
            callback(result, True)

        return result

    def scan_Async(self, paths, maxDepth = 10, excludes = [], callback = None):
        """Scans the given file or directory into the database, provided there
        is/are language scanners for scanned files.
        @param paths List full paths to the files or directories to scan.
        @param maxDepth Optional maximum directory depth to scan. The default
                         value is 10.
        @param excludes Optional list of directories to exclude in the scan. The
                       default value is the empty list.
        @param callback Callback to call when a file is done scanning
        """
        log.debug("Scanning %s, depth: %d" % (", ".join(paths), maxDepth))

        paths = self._scanSummary(paths, maxDepth, excludes, limit = False)
        if not paths["count"]:
            if callback:
                callback(None, True)
            return

        paths = paths["paths"]

        enqueued = { "count": 0 } # has to be dict cause of variable scoping in python2
        def enqueue(path, scanner):
            #log.debug("enqueue %s" % path)

            enqueued["count"] = enqueued["count"] + 1

            def _callback(path, done = False):
                if not callback:
                    return
                if not done:
                    callback({ "path": path }, False)
                else:
                    enqueued["count"] = enqueued["count"] - 1
                    if enqueued["count"] is 0:
                        callback(None, True)

            Scanner.enqueue(path, scanner, _callback)

        for entry in paths:
            path, language = entry
            language = Languages.getLanguage(language)
            enqueue(path, language.scanner)

    def _getEnv(self, path, parentPath, importPaths, language):
        env = {"FILENAME": path}
        if language.extrapaths:
            env[language.extrapaths] = "%s%s%s" % (parentPath, os.pathsep, importPaths)

        return env

    def keepalive(self):
        """
        This just serves to have our socket handle requests consistently so
        the parent thread can identify any issues
        """
        #log.debug("keepalive")
        pass

    def _weighed(self, symbols, query, path = None, language = None):

        weights = {
            "fullmatch": 50,
            "pathmatch": 10,
            "startmatch": 10,
            "sequentialmatch": 10,
            "languagematch": 20
        }

        query = query.lower()
        queryWords = [_f for _f in re.split("\W+", query) if _f]

        def weigh(symbol):
            if symbol["weight"] != -1:
                return symbol["weight"]

            name = symbol["name"].lower()
            weight = 0

            if name == query:
                weight = weight + weights["fullmatch"] + weights["startmatch"] + weights["sequentialmatch"]

            elif name.startswith(query):
                weight = weight + weights["startmatch"]

                lastIndex = -1
                try:
                    for word in queryWords:
                        idx = name.index(word)
                        if idx < lastIndex:
                            break
                    else:
                        weight = weight + weights["sequentialmatch"]
                except ValueError:
                    pass

            if path and symbol["path"] == path:
                weight = weight + weights["pathmatch"]

            if language and symbol["language"] == language:
                weight = weight + weights["languagematch"]

            return weight

        for symbol in symbols:
            symbol["weight"] = weigh(symbol)

        def compare(a,b):
            if a["weight"] == b["weight"]:
                return len(a["name"]) - len(b["name"])
            return b["weight"] - a["weight"]

        return sorted(symbols, key=cmp_to_key(compare))

    def getKeywords(self, languageName):
        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            return

        return language.keywords

    def getCatalogs(self, languageName):
        """Returns a list/tuple of known catalogs for the given language."""
        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        return language.catalogs

    def loadCatalog(self, languageName, catalog):
        """Loads the given catalog (one of the ones returned from
        'getCatalogs()') into the given language's scanner.
        """
        language = Languages.getLanguage(languageName)

        if not language:
            log.debug("Language not supported: %s " % languageName)
            return

        if not language.scanner:
            log.debug("Language not supported: %s " % languageName)
            return

        language.scanner.addToBuiltInScope(catalog)

import logging
import os
import shutil
import threading
from config import Config

from peewee import *
from playhouse.apsw_ext import *

log = logging.getLogger("codeintel.db.database")
#log.setLevel(logging.DEBUG)

VERSION = 2

class Database:

    initialized = False
    conn = Proxy()
    lock = threading.Lock()

    @staticmethod
    def initialize(database, closure_ext):
        from db.model import Symbol, SymbolClosure, File, Doc, Meta

        if Database.initialized:
            log.error("Database is already initialized")
            return

        isExistingDb = True

        if database and "memory" not in database:
            if not os.path.isfile(database):
                sourceDb = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "codeintel.db"))
                log.info(sourceDb)
                if os.path.isfile(sourceDb):
                    log.info("Copying database from %s" % sourceDb)
                    shutil.copyfile(sourceDb, database)
                else:
                    isExistingDb = False
                    log.warn("Starting codeintel without a seeded database")

        Database.conn.initialize(APSWDatabase(database, pragmas=(
            ('journal_mode', 'WAL'),
            ('synchronous', 'OFF'),
            ('cache_size', '20000'),
            ('temp_store', 'MEMORY')
        ) ))

        Database.conn.load_extension(closure_ext)
        Database.conn.create_tables([File, Symbol, SymbolClosure, Doc, Meta], safe=True)

        Database.update(isExistingDb)

        log.info("Database initialized")

    @staticmethod
    def update(isExistingDb):
        from db.model import Meta

        try:
            meta = Meta.select().get()
        except Meta.DoesNotExist:
            if not isExistingDb:
                Meta.create(version=VERSION)
                return
            else:
                meta = Meta.create(version=1)

        while meta.version < VERSION:
            meta.version = meta.version + 1

            if meta.version == 2:
                Database.conn.execute_sql('CREATE INDEX `symbol_name_type` ON `symbol` ( `name`, `symbol_type` )')

        meta.save()

    @staticmethod
    def writeFileScope(filename, scope):
        """Writes to the database the given AbstractScope for the given
        filename.
        If the file already exists in the database, its existing AbstractSymbols
        are deleted first.
        @param filename String filename the scope comes from.
        @param scope AbstractScope object produced after parsing the file.
        """
        from symbols import AbstractScope, AbstractSymbol, AbstractClass, AbstractFunction
        from db.model import File, Symbol, Doc

        if not isinstance(scope, AbstractScope):
            raise TypeError("scope must be derived from AbstractScope (got '%s')" % scope.__class__.__name__)
        elif not Database.conn:
            raise RuntimeError("Database connection not initialized")

        # Normalize filename. (Needed primarily for Windows.)
        filename = os.path.normcase(filename)
        log.debug("Writing filename '%s' to database", filename)

        File.delete().where(File.path==filename).execute()
        file = File.create(path=filename)

        try:
            Database.lock.acquire(True)
            with Database.conn.atomic():
                Symbol.delete().where(Symbol.file==file).execute()
                def write_scope(scope, parent=None):
                    if isinstance(scope, AbstractSymbol):
                        name = scope.name
                        symbol_type = Symbol.getSymbolType(scope.__class__)
                        if not symbol_type:
                            log.warn("Unrecognized symbol type for AbstractSymbol '%s'; expected type '%s' is not implemented in 'db.model.symbol')" % (name, scope.__class__.__mro__[1].__name__))
                            return
                        if isinstance(scope, AbstractClass) and scope.superclassName:
                            if isinstance(scope.superclassName, str):
                                type_hint = scope.superclassName
                            else:
                                type_hint = ",".join(scope.superclassName)
                        elif isinstance(scope, AbstractFunction):
                            type_hint = scope.returnType
                        else:
                            type_hint = scope.type
                        if scope.ctx:
                            line = scope.ctx.line
                        else:
                            #log.warn("No context given for AbstractSymbol '%s'" % name)
                            line = None
                        parent = Symbol.create(file_id=file.id, name=name, symbol_type=symbol_type, type_hint=type_hint, line=line, parent=parent)
                        if scope.ctx and (scope.ctx.documentation or scope.ctx.signature):
                            Doc.create(symbol=parent, summary=scope.ctx.documentation or "", signature=scope.ctx.signature)
                    for member in list(scope.members.values()):
                        if isinstance(member, AbstractScope):
                            write_scope(member, parent)
                        else:
                            name = member.name
                            symbol_type = Symbol.getSymbolType(member.__class__)
                            if not symbol_type:
                                log.warn("Unrecognized symbol type for AbstractSymbol '%s'; expected type '%s' is not implemented in 'db.model.symbol')" % (name, scope.__class__.__mro__[1].__name__))
                                continue
                            type_hint = member.type
                            if member.ctx:
                                line = member.ctx.line
                            else:
                                #log.warn("No context given for AbstractSymbol '%s'" % name)
                                line = None
                            symbol = Symbol.create(file_id=file.id, name=name, symbol_type=symbol_type, type_hint=type_hint, line=line, parent=parent)
                            if member.ctx and (member.ctx.documentation or member.ctx.signature):
                                Doc.create(symbol=symbol, summary=member.ctx.documentation or "", signature=member.ctx.signature)
                write_scope(scope)
        finally:
            Database.lock.release()

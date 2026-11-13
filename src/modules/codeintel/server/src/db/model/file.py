from peewee import *
from playhouse.apsw_ext import DateTimeField
from db import Database
from db.model import Model
import datetime
import os
import threading

class File(Model):
    
    id = PrimaryKeyField()
    path = CharField(index=True)
    created = DateTimeField(default=datetime.datetime.now)

    cache = None

    class Meta:
        database = Database.conn

    def save(self, *args, **kwargs):
        File.cache = None
        return super(File, self).save(*args, **kwargs)

    @classmethod
    def delete(cls, *args, **kwargs):
        File.cache = None
        return super(File, cls).delete(*args, **kwargs)

    @classmethod
    def _updateCache(cls):
        if threading.current_thread().name == "Scanner":
            return

        if not File.cache:
            File.cache = {}

            for f in File.select():
                File.cache[f.path] = f.id

    @classmethod
    def exists(cls, filename):
        """Returns whether or not the given filename exists in the database.
        @param filename String filename to look for.
        @usage fileExists(PYTHON2_STDLIB_FILE)
        @return True or False
        """
        if not isinstance(filename, str):
            raise TypeError("filename must be a string (got '%s')" % filename.__class__.__name__)

        cls._updateCache()

        if File.cache is None:
            try:
                File.get(File.path == filename)
                return True
            except File.DoesNotExist:
                return False

        filename = os.path.normcase(filename) # normalized
        return filename in File.cache
    
    @classmethod
    def idFromPath(cls, filename):
        """Returns whether or not the given filename exists in the database.
        @param filename String filename to look for.
        @usage fileExists(PYTHON2_STDLIB_FILE)
        @return True or False
        """
        if not isinstance(filename, str):
            raise TypeError("filename must be a string (got '%s')" % filename.__class__.__name__)

        # Bypass the cache if we're on the scanner thread, as otherwise the cache would keep getting rebuilt
        if threading.current_thread().name == "Scanner":
            db_file = File.get(File.path == filename)
            return db_file.id

        cls._updateCache()
        
        filename = os.path.normcase(filename) # normalized
        if not filename in File.cache:
            return None
        
        return File.cache[filename]
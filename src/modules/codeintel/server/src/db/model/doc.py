from peewee import *
from playhouse.sqlite_ext import ClosureTable

from db import Database
from db.model import Model, Symbol

class Doc(Model):

    symbol = ForeignKeyField(Symbol, index=True)
    summary = CharField()
    # Functions have signatures (name plus arguments).
    signature = CharField(null=True)

    class Meta:
        database = Database.conn
        only_save_dirty = True

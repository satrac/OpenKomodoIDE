from peewee import *
from playhouse.sqlite_ext import ClosureTable

from db import Database

class Meta(Model):

    version = IntegerField()

    class Meta:
        database = Database.conn

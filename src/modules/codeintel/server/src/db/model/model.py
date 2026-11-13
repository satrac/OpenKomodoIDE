import peewee
from peewee import *

class Model(peewee.Model):

    def has_dirty_parent(self):
        for field in self.dirty_fields:
            if not isinstance(field, ForeignKeyField):
                continue
            model = getattr(self, field.name, None)
            if isinstance(model, Model) and model.is_dirty():
                return True
        return False

    # Peewee 3 resolves model-valued foreign keys during save().  Peewee 2
    # exposed the pending related objects via _obj_cache; that attribute no
    # longer exists, so rely on the peewee 3 behaviour here.
    def save(self, **kwargs):
        return super(Model, self).save(**kwargs)

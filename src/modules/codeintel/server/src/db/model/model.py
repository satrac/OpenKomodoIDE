import peewee
from peewee import *

class Model(peewee.Model):

    def has_dirty_parent(self):
        for field in self.dirty_fields:
            if not isinstance(field, ForeignKeyField):
                continue
            
            model = self._obj_cache.get(field.name)
            
            if not isinstance(model, Model):
                continue
            
            if model.is_dirty():
                return True
        
        return False
    
    # Extend save to automatically infer the foreign table row id when it's
    # referenced via a model
    def save(self, **kwargs):
        for field in self.dirty_fields:
            if not isinstance(field, ForeignKeyField):
                continue
            
            model = self._obj_cache.get(field.name)
            
            if not isinstance(model, Model):
                continue
            
            model.save()
            setattr(self, field.db_column, getattr(model, field.to_field.db_column))
        
        super(Model, self).save(**kwargs)
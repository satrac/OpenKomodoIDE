// Copyright 2002 by Brian Quinlan <brian@sweetapp.com>
// The License.txt file describes the conditions under which this 
// software may be distributed.

#include "PyPropSet.h"

static char keys_doc[] = 
"keys() -> list of PropertySet's keys";

static char values_doc[] = 
"values() -> list of PropertySet's values";

static int
PyPropSet_set_from_map(PyPropSet *self, PyObject *pyPropertyMap);

PyObject*
PyPropSet_new(PyObject *, PyObject* args)
{
    PyPropSet * pyPropSet;
    PyObject * pyPropertyMap = NULL;

    if (!PyArg_ParseTuple(args, "|O", &pyPropertyMap))
        return NULL;

    pyPropSet = PyObject_New(PyPropSet, &PyPropSetType);
    if (pyPropSet == NULL) {
        return NULL;
    }

    pyPropSet->propSet = new PropSetEx();

    if (pyPropertyMap != NULL) {
        if (PyPropSet_set_from_map(pyPropSet, pyPropertyMap) == -1) {
            Py_DECREF(pyPropSet);
            return NULL;
        }

    }

    return (PyObject*) pyPropSet;
}

static void
PyPropSet_dealloc(PyPropSet* self)
{
    delete self->propSet;
    PyObject_Del(self);
}

static PyObject *
PyPropSet_subscript(PyPropSet *self, PyObject *key)
{
    SString     value;

    if (!PyString_Check(key)) {
        PyErr_Format(PyExc_TypeError, "expected string, %.200s found",
           Py_TYPE(key)->tp_name);
        return NULL;
    }
    
    value = self->propSet->Get(PyString_AS_STRING(key));
    return Py_BuildValue("s#", value.c_str(), value.length());
}

static int
PyPropSet_ass_subscript(PyPropSet *self, PyObject *key, PyObject *value)
{
    PyObject *  pyValueStr = NULL;
    char *      valueStr;
    int         valueSize;

    if (!PyString_Check(key)) {
        PyErr_Format(PyExc_TypeError, "expected string, %.200s found",
           Py_TYPE(key)->tp_name);
        return -1;
    }

    if (value == NULL) {
        valueStr = (char *) "";
        valueSize = 0;
    }
    else
    {
        pyValueStr = PyObject_Str(value);
        if (pyValueStr == NULL) {
            return -1;
        }

        valueStr = PyString_AsString(pyValueStr);
        valueSize = PyString_Size(pyValueStr);

        if ((valueStr == NULL) || (valueSize == -1)) {
            goto onError;
        }
    }

    self->propSet->Set(PyString_AS_STRING(key), valueStr, -1, valueSize);
    Py_XDECREF(pyValueStr);

    return 0;

onError:
    Py_XDECREF(pyValueStr);
    return -1;

}

static int
PyPropSet_set_from_map(PyPropSet *self, PyObject *pyPropertyMap)
{
    PyObject *  pyPropertyList = NULL;
    PyObject *  pyItem = NULL;
    PyObject *  pyKey;
    PyObject *  pyValue;
    int         size;
    int         i;

    if (!PyMapping_Check(pyPropertyMap)) {
        PyErr_Format(PyExc_TypeError, "expected dictionary, %.200s found",
           Py_TYPE(pyPropertyMap)->tp_name);
        goto onError;
    }

    pyPropertyList = PyMapping_Items(pyPropertyMap);
    if (pyPropertyList == NULL) {
        goto onError;
    }

    if (!PySequence_Check(pyPropertyList)) {
        PyErr_Format(PyExc_TypeError, "expected a list, %.200s found",
           Py_TYPE(pyPropertyList)->tp_name);
        goto onError;
    }

    size = PySequence_Size(pyPropertyList);
    if (size == -1) {
        goto onError;
    }

    for (i = 0; i < size; ++i) {
        pyItem = PySequence_GetItem(pyPropertyList, i);

        if (pyItem == NULL) {
            goto onError;
        }

        if (!PyArg_ParseTuple(pyItem, "OO", &pyKey, &pyValue)) {
            PyErr_Format(PyExc_TypeError, "expected a 2-tuple, %.200s found",
               Py_TYPE(pyPropertyMap)->tp_name);
            goto onError;
        }

        
        if (PyPropSet_ass_subscript(self, pyKey, pyValue) == -1) {
            goto onError;
        }

        Py_DECREF(pyItem);
    }
    
    Py_DECREF(pyPropertyList);

    return 0;
onError:
    Py_XDECREF(pyPropertyList);
    Py_XDECREF(pyItem);
    return -1;
}

static PyObject *
PyLexState_keyvalue_wrap(PyPropSet *self, PyObject *args, bool want_key)
{
    if (!PyArg_ParseTuple(args, ""))
        return NULL;

    PyObject * list = NULL;
    PyObject * item = NULL;
    char * key;
    char * value;
    bool get;

    list = PyList_New(0);
    if (list == NULL) {
        return NULL;
    }

    get = self->propSet->GetFirst(&key, &value);
    while (get) {
        if (want_key) {
            item = PyString_FromString(key);
        } else {
            item = PyString_FromString(value);
        }

        if (item == NULL) {
            goto onError;
        }

        if (PyList_Append(list, item) == -1) {
            goto onError;
        }

        Py_DECREF(item);
        get = self->propSet->GetNext(&key, &value);
    }

    return list;
onError:
    Py_XDECREF(list);
    Py_XDECREF(item);
    return NULL;
}

static PyObject *
PyLexState_keys(PyPropSet *self, PyObject *args)
{
    return PyLexState_keyvalue_wrap(self, args, true);
}

static PyObject *
PyLexState_values(PyPropSet *self, PyObject *args)
{
    return PyLexState_keyvalue_wrap(self, args, false);
}

static PyMethodDef PyPropSet_methods[] = 
{
    {"keys", (PyCFunction) PyLexState_keys, METH_VARARGS, keys_doc},
    {"values", (PyCFunction) PyLexState_values, METH_VARARGS, values_doc},
    { NULL, NULL }
};

PyMappingMethods PyPropSet_as_mapping = {
    0,                                          /* mp_length */
    (binaryfunc) PyPropSet_subscript,           /* mp_subscript */
    (objobjargproc) PyPropSet_ass_subscript,    /* mp_ass_subscript */
};

PyTypeObject PyPropSetType = {};

void
initPyPropSet(void)
{
    PyTypeObject *t = &PyPropSetType;
    memset(t, 0, sizeof(*t));
    PyObject_Init((PyObject *)t, &PyType_Type);
    t->tp_name = "PropertySet";
    t->tp_basicsize = sizeof(PyPropSet);
    t->tp_dealloc = (destructor) PyPropSet_dealloc;
    t->tp_flags = Py_TPFLAGS_DEFAULT;
    t->tp_methods = PyPropSet_methods;
    t->tp_as_mapping = &PyPropSet_as_mapping;
    if (PyType_Ready(t) < 0)
        PyErr_Clear();
}

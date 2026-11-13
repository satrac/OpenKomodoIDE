// Copyright 2002 by Brian Quinlan <brian@sweetapp.com>
// The License.txt file describes the conditions under which this 
// software may be distributed.

#include <stdio.h>  // For NULL, for WordList.h
#include <ctype.h> // For ctypes..., for WordList.h
#include <assert.h>

#include "ILexer.h"
#include "Scintilla.h"
#include <SciLexer.h>
#include "WordList.h"
#include <LexAccessor.h>
#include <Accessor.h>

#include "PyWordList.h"

PyObject*
PyWordList_new(PyObject *, PyObject* args)
{
    PyWordList*     pyWordList;
    char*           wordStr = NULL;

    if (!PyArg_ParseTuple(args, "|s", &wordStr))
        return NULL;

    pyWordList = PyObject_New(PyWordList, &PyWordListType);
    if (wordStr) {
        pyWordList->wordListAsString = PyString_FromString(wordStr);
        Py_INCREF(pyWordList->wordListAsString);
    } else {
        pyWordList->wordListAsString = NULL;
    }

    return (PyObject*) pyWordList;
}

static PyMethodDef PyWordList_methods[] = 
{
    { NULL, NULL }
};


static PyObject *
PyWordList_get_words(PyWordList *self, void *closure)
{
    if (self->wordListAsString) {
        Py_INCREF(self->wordListAsString);
        return self->wordListAsString;
    }
    Py_RETURN_NONE;
}

static PyGetSetDef PyWordList_getset[] = {
    {"words", (getter) PyWordList_get_words, NULL, NULL, NULL},
    { NULL }
};

static void
PyWordList_dealloc(PyWordList* self)
{
    Py_XDECREF(self->wordListAsString);
    PyObject_Del(self);
}

PyTypeObject PyWordListType = {};

void
initPyWordList(void)
{
    PyTypeObject *t = &PyWordListType;
    memset(t, 0, sizeof(*t));
    PyObject_Init((PyObject *)t, &PyType_Type);
    t->tp_name = "WordList";
    t->tp_basicsize = sizeof(PyWordList);
    t->tp_dealloc = (destructor) PyWordList_dealloc;
    t->tp_flags = Py_TPFLAGS_DEFAULT;
    t->tp_methods = PyWordList_methods;
    t->tp_getset = PyWordList_getset;
    if (PyType_Ready(t) < 0)
        PyErr_Clear();
}

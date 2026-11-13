// Python 3 compatibility helpers for the SilverCity extension.
#ifndef SILVERCITY_PY3COMPAT_H_
#define SILVERCITY_PY3COMPAT_H_

#include <Python.h>
#include <string.h>

static inline PyObject *
SC_FindMethod(PyMethodDef *methods, PyObject *self, const char *name)
{
    for (PyMethodDef *m = methods; m->ml_name != NULL; ++m) {
        if (strcmp(m->ml_name, name) == 0)
            return PyCFunction_New(m, self);
    }
    PyErr_Format(PyExc_AttributeError,
                 "'%.200s' object has no attribute '%s'",
                 Py_TYPE(self)->tp_name, name);
    return NULL;
}

#define Py_FindMethod(methods, self, name) \
    SC_FindMethod((methods), (self), (name))

#define PyString_Check(op) \
    (PyUnicode_Check(op) || PyBytes_Check(op))

static inline const char *
SC_StringAsUTF8(PyObject *op)
{
    if (op == NULL)
        return NULL;
    if (PyBytes_Check(op))
        return PyBytes_AS_STRING(op);
    if (PyUnicode_Check(op))
        return PyUnicode_AsUTF8(op);
    PyErr_Format(PyExc_TypeError, "expected str or bytes, not %.200s",
                 Py_TYPE(op)->tp_name);
    return NULL;
}

#define PyString_AsString(op) ((char *)SC_StringAsUTF8((PyObject *)(op)))
#define PyString_AS_STRING(op) ((char *)SC_StringAsUTF8((PyObject *)(op)))
#define PyString_Size(op) \
    (PyBytes_Check(op) ? PyBytes_GET_SIZE(op) : PyUnicode_GET_LENGTH(op))
#define PyString_FromString(s) PyUnicode_FromString(s)
#define PyString_FromFormat PyUnicode_FromFormat

#endif // SILVERCITY_PY3COMPAT_H_

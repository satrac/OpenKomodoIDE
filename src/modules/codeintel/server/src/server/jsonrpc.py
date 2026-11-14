import json
import logging
import inspect
import re
import base64
import os
import cProfile
import threading

from config import Config

log = logging.getLogger("JsonRPC")
#log.setLevel(10)

ERROR_PARSING = -32700
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL = -32603

class JsonRPC:

    methods = {}

    def __init__(self, response):
        self.response = response

    def process(self, data):
        try:
            data = base64.b64decode(data)
            log.debug("Received: %s" % data)
            request = json.loads(str(data, errors='replace'))
        except Exception as e:
            log.exception("Failed parsing JSON: %s" % data)
            self.respondError("Failed parsing request JSON", ERROR_PARSING)
            return

        if not self.validate_request(request):
            return

        method = JsonRPC.methods[request["method"]]

        # Pass to process_async if this method is async
        if method["async"]:
            thread = threading.Thread(
                target=self.process_async,
                args=(request,),
                name="jsonrpc process async %s" % request["method"]
            )
            thread.daemon = False
            thread.start()
            return

        # Handle the request synchronously
        try:
            if Config.get("profiling"):
                profiler = cProfile.Profile()
                profiler.enable()
            result = method["function"](*request["params"])
            if Config.get("profiling"):
                profiler.disable()
                profiler.dump_stats(os.path.join(Config.get("profiling"), "codeintel.%s.prof" % request["method"]))
        except Exception as e:
            log.exception("Failed calling API method: %s" % request["method"])
            self.respondError("Failed calling API method: %s, message: %s" % (request["method"], str(e)),
                              ERROR_INTERNAL, request["id"])
            return

        self.respond(result, request["id"], True)

    def process_async(self, request):
        def callback(result, final = True):
            self.respond(result, request["id"], final)

        params = request["params"]
        params.append(callback)

        method = JsonRPC.methods[request["method"]]

        try:
            if Config.get("profiling"):
                profiler = cProfile.Profile()
                profiler.enable()
            result = method["function"](*params)
            if Config.get("profiling"):
                profiler.disable()
                profiler.dump_stats(os.path.join(Config.get("profiling"), "codeintel.%s.prof" % request["method"]))
        except Exception as e:
            log.exception("Failed calling API async method: %s" % request["method"])
            self.respondError("Failed calling API async method: %s, message: %s" % (request["method"], str(e)),
                              ERROR_INTERNAL, request["id"])

    def validate_request(self, request):
        missing = []
        fields = ["method", "params", "id"]
        for field in fields:
            if field not in list(request.keys()):
                missing.append(field)

        if len(missing):
            self.respondError("Request is missing the following fields: %s" % ", ".join(missing),
                              ERROR_INVALID_REQUEST, request["id"])
            return False

        if request["method"] not in JsonRPC.methods:
            self.respondError("Method not found: %s" % request["method"],
                              ERROR_METHOD_NOT_FOUND, request["id"])
            return False

        func = JsonRPC.methods[request["method"]]["function"]

        reqArgLength = len(request["params"])
        argLength = func.__code__.co_argcount
        if reqArgLength > argLength:
            self.respondError("Method %s takes %d arguments, %d given" % (request["method"], argLength, reqArgLength),
                              ERROR_INVALID_PARAMS, request["id"])
            return False

        return True

    def respond(self, result, identifier, final = True):
        complete = 1
        if not final:
            complete = 0

        ob = {
            "jsonrpc": "2.0",
            "result": result,
            "id": identifier,
            "complete": complete
        }

        try:
            data = json.dumps(ob)
            log.debug("Responding: %s" % data)
            data = base64.b64encode(data.encode("utf-8"))
        except Exception as e:
            log.exception("Failed to encode data as JSON, id: %d" % identifier)
            return

        self.response(data)

    def respondError(self, message, code = 0, identifier = 0):
        ob = {
            "jsonrpc": "2.0",
            "error": {
                "code": code,
                "message": message
            },
            "id": identifier
        }

        try:
            data = json.dumps(ob)
            data = base64.b64encode(data.encode("utf-8"))
        except Exception as e:
            log.exception("Failed to encode error data as JSON, error code: %d, id: %d" % (code, identifier))
            return

        self.response(data)

    @staticmethod
    def registerMethod(func, name = None, is_async = False):
        if not name:
            name = func.__name__

        JsonRPC.methods[name] = { "function": func, "async": is_async}

    @staticmethod
    def registerService(instance):
        names = dir(instance)
        for name in names:
            value = getattr(instance, name)
            if not name.startswith("_") and inspect.ismethod(value):
                nameClean = re.sub(r'_?[a|A]sync$', '', name)
                JsonRPC.registerMethod(
                    value,
                    name = nameClean,
                    is_async = name.lower().endswith("async")
                )

# api decorators
def api(func):
    JsonRPC.registerMethod(func)

def api_async(func):
    JsonRPC.registerMethod(func, is_async = True)

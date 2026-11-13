import os
import platform

class Config:
    
    initialized = False
    
    default = {
        "arch": "%s-%s" % (platform.system(), platform.architecture()[0]),
        "root": os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        "database_conn": ":memory:",
        "keepalive_timeout": 15,
        "thread_max_age": 300 # 5 minutes
    }
    
    config = {}
    
    @staticmethod
    def use(c):
        if type(c) == dict:
            Config.config = c
            
        if type(c) == str:
            with open(c, 'r') as f:
                config = f.read()

            Config.config = json.loads(config)

    @staticmethod
    def get(key, fallback = None):
        return Config.config.get(key, Config.default.get(key, fallback))

    @staticmethod
    def set(key, value):
        Config.config[key] = value

if platform.system() == "Windows":
    closure_name = "closure.dll"
else:
    closure_name = "closure.so"

# Detect closure.so path
root_closure_path = os.path.join(Config.default["root"], closure_name)
env_closure_path = os.path.join(Config.default["root"], "env", closure_name)
if os.path.exists(root_closure_path):
    Config.default["closure_ext_path"] = root_closure_path.replace("\\", "/")
elif os.path.exists(env_closure_path):
    Config.default["closure_ext_path"] = env_closure_path.replace("\\", "/")

if not "closure_ext_path" in Config.default:
    raise Exception("Could not find %s in any of the following paths: \n%s,\n%s" %
                    (closure_name, root_closure_path, env_closure_path))

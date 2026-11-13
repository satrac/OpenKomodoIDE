import sys
import os

cwd = os.path.dirname(__file__)
include = os.path.join(cwd, "..", "env", "lib", "python2.7", "site-packages")
sys.path.append(os.path.abspath(include))
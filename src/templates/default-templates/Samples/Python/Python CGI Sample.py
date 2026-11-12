#!/usr/bin/env python3
import cgi

print('Content-Type: text/html')
print() # Blank line marking end of HTTP headers

cgiParameters = cgi.FieldStorage()

# Check to see that we have our required parameters
if not ("name" in cgiParameters and "address" in cgiParameters):
    print("<H1>Error</H1>")
    print("Please fill in the name and address fields.")
else:
    print("<p>name:", form["name"].value)
    print("<p>addr:", form["addr"].value)

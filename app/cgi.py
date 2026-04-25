"""
Minimal stdlib cgi compatibility shim for Python 3.13+.

Django 4.0 still imports ``cgi.parse_header`` during request parsing.
Python 3.13 removed the stdlib cgi module, so we provide only the tiny
surface area the project needs for local test/runtime compatibility.
"""
import re
from email.message import Message


def parse_header(line):
    if not line:
        return "", {}

    message = Message()
    message["content-type"] = line
    params = message.get_params(header="content-type", failobj=[])
    if not params:
        return line, {}

    main_value = params[0][0]
    options = {key: value for key, value in params[1:]}
    return main_value, options


def valid_boundary(boundary):
    if not boundary:
        return False

    if isinstance(boundary, bytes):
        pattern = rb"^[ -~]{0,200}[!-~]$"
    else:
        pattern = r"^[ -~]{0,200}[!-~]$"

    return re.match(pattern, boundary)

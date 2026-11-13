"""
AngularJS CSS extensions.
"""

import textwrap

CSS_ANGULAR_DATA = {
    'ng-form': {},
    'ng-valid': {},
    'ng-invalid': {},
    'ng-pending': {},
    'ng-pristine': {},
    'ng-dirty': {},
    'ng-bind': {},
    'ng-class': {},
    'ng-class-odd': {},
    'ng-class-even': {},
    'ng-cloak': {},
    'ng-include': {},
    #'onload': {},
    #'autoscroll': {},
    'ng-init': {},
    'ng-touched': {},
    'ng-untouched': {},
    'ng-empty': {},
    'ng-not-empty': {},
    'ng-non-bindable': {},
    'ng-show': {},
    'ng-hide': {},
    'ng-style': {},
    'ng-transclude': {},
}

CSS_ANGULAR_SPECIFIC_ATTRS_DICT = {}
CSS_ANGULAR_SPECIFIC_CALLTIP_DICT = {}
for attr, details in list(CSS_ANGULAR_DATA.items()):
    values = details.get("values", {})
    attr_completions = sorted(values.keys())
    if attr_completions:
        CSS_ANGULAR_SPECIFIC_ATTRS_DICT[attr] = attr_completions
    else:
        CSS_ANGULAR_SPECIFIC_ATTRS_DICT[attr] = None
    description = details.get("description")
    if description:
        desc_lines = textwrap.wrap(description, width=60)
        if values:
            desc_lines.append("")
            for value, attr_desc in list(values.items()):
                attr_desc = "  %r: %s" % (value, attr_desc)
                attr_desc_lines = textwrap.wrap(attr_desc, width=50)
                for i in range(len(attr_desc_lines)):
                    attr_line = attr_desc_lines[i]
                    if i > 0:
                        attr_line = "        " + attr_line
                    desc_lines.append(attr_line)
        CSS_ANGULAR_SPECIFIC_CALLTIP_DICT[attr] = "\n".join(desc_lines).encode("ascii", 'replace')

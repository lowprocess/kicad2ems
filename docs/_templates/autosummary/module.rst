{{ fullname | escape | underline }}

.. automodule:: {{ fullname }}

{# a package's __main__ (python -m <package>) is left out: importing it runs
   the program #}
{% if modules %}
.. rubric:: Modules

.. autosummary::
   :toctree:
   :recursive:
{% for item in modules if not item.endswith('.__main__') %}
   {{ item }}
{%- endfor %}
{% endif %}

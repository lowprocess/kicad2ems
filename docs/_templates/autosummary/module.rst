{{ fullname | escape | underline }}

.. automodule:: {{ fullname }}
{%- if fullname in ('kicad2ems.api.solver', 'kicad2ems.api.frontend', 'kicad2ems_gui.api') %}
   :no-members:

Each name, and where it is defined: :doc:`../api-names`.
{%- endif %}

{# a package's __main__ (python -m <package>) is left out: importing it runs the program #}
{% if modules %}
.. rubric:: Modules

.. autosummary::
   :toctree:
   :recursive:
{% for item in modules if not item.endswith('.__main__') %}
   {{ item }}
{%- endfor %}
{% endif %}

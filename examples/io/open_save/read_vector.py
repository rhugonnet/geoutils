"""
Open/save a vector
==================

This example demonstrates the instantiation of a vector through :class:`geoutils.Vector` and saving with :func:`~geoutils.Vector.save`.
"""

import geoutils as gu

# %%
# We open an example vector.
vect = gu.Vector(gu.examples.get_path("everest_rgi_outlines"))
vect

# %%
#
# .. note::
#        A vector can also be instantiated with a :class:`geopandas.GeoDataFrame`, see :ref:`sphx_glr_io_examples_import_export_import_vector.py`.
#
# We can print more info on the vector.
print(vect)

# %%
# Let's plot by vector area
vect.show(column="Area", cbar_title="Area (km²)")

# %%
# Finally, a vector is saved using :func:`~geoutils.Vector.save`.

# vect.save("myvector.shp")
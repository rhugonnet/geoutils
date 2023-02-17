(vector-class)=

# The georeferenced vector (`Vector`)

## Object definition

The Vector class builds upon the great functionalities of [GeoPandas](https://geopandas.org/), with the aim to bridge the gap between vector and raster files.
It uses `geopandas.GeoDataFrame` as a base driver, accessible through `Vector.ds`.

## Open and save

```{literalinclude} code/vector-basics_open_file.py
:lines: 2-6
```

Printing the Vector shows the underlying `GeoDataFrame` and some extra statistics:

```python
print(outlines)
```

```{eval-rst}
.. program-output:: $PYTHON -c "exec(open('code/vector-basics_open_file.py').read()); print(outlines)"
        :shell:

```

````{margin} 
**Example of vector from {func}`~geoutils.Vector.info`:**

```{literalinclude} code/index_vect_info.py
    :lines: 11-12
    :language: python
```

```{eval-rst}
.. program-output:: $PYTHON code/index_vect_info.py
        :shell:
```


````

Masks can easily be generated for use with Rasters:

```{literalinclude} code/vector-basics_open_file.py
:lines: 8-13
```

We can prove that glaciers are bright (who could have known!?) by masking the values outside and inside of the glaciers:

```python
print(f"Inside: {image.data[mask].mean():.1f}, outside: {image.data[~mask].mean():.1f}")
```

```{eval-rst}
.. program-output:: $PYTHON -c "exec(open('code/vector-basics_open_file.py').read()); print(f'Inside: {image.data[mask].mean():.1f}, outside: {image.data[~mask].mean():.1f}')"
        :shell:
```

TODO: Add rasterize text.

```{eval-rst}
.. minigallery:: geoutils.Raster
        :add-heading:
        :heading-level: -
```


## Arithmetic


## Reproject


## Crop


## Rasterize

The {func}`~geoutils.Vector.rasterize` operation to convert from {class}`~geoutils.Vector` to {class}`~geoutils.Raster` inherently requires a 
{attr}`~geoutils.Raster.res` or {attr}`~geoutils.Raster.shape` attribute to define the grid. While those can be passed on their own.

## Proximity


## Create a `Mask`


## Buffering


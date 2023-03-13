"""
geoutils.vectortools provides a toolset for working with vector data.
"""
from __future__ import annotations

import os
import pathlib
import warnings
from collections import abc
from numbers import Number
from typing import Any, Literal, TypeVar, overload

import fiona
import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import rasterio as rio
import rasterio.errors
import shapely
from mpl_toolkits.axes_grid1 import make_axes_locatable
from rasterio import features, warp
from rasterio.crs import CRS
from scipy.spatial import Voronoi
from shapely.geometry.polygon import Polygon

import geoutils as gu
from geoutils.projtools import _get_bounds_projected, bounds2poly

# This is a generic Vector-type (if subclasses are made, this will change appropriately)
VectorType = TypeVar("VectorType", bound="Vector")


class Vector:
    """
    The georeferenced vector

     Main attributes:
        ds: :class:`geopandas.GeoDataFrame`
            Geodataframe of the vector.
        crs: :class:`pyproj.crs.CRS`
            Coordinate reference system of the vector.
        bounds: :class:`rio.coords.BoundingBox`
            Coordinate bounds of the vector.

    All other attributes are derivatives of those attributes, or read from the file on disk.
    See the API for more details.
    """

    def __init__(self, filename_or_dataset: str | pathlib.Path | gpd.GeoDataFrame):
        """
        Instantiate a vector from a filename or geopandas geodataframe.

        :param filename_or_dataset: The filename or geodataframe.
        """

        if isinstance(filename_or_dataset, (str, pathlib.Path)):
            with warnings.catch_warnings():
                # This warning shows up in numpy 1.21 (2021-07-09)
                warnings.filterwarnings("ignore", ".*attribute.*array_interface.*Polygon.*")
                ds = gpd.read_file(filename_or_dataset)
            self._ds = ds
            self._name: str | gpd.GeoDataFrame | None = filename_or_dataset
        elif isinstance(filename_or_dataset, gpd.GeoDataFrame):
            self._ds = filename_or_dataset
            self._name = None
        else:
            raise TypeError("Filename argument should be a string, Path or geopandas.GeoDataFrame.")

        self._crs = self.ds.crs

    def __repr__(self) -> str:
        """Convert vector to string representation."""

        # Get the representation of ds
        str_ds = "\n       ".join(self.__str__().split("\n"))

        s = str(
            self.__class__.__name__
            + "(\n"
            + "  ds="
            + str_ds
            + "\n  crs="
            + self.crs.__str__()
            + "\n  bounds="
            + self.bounds.__str__()
            + ")"
        )

        return s

    def _repr_html_(self) -> str:
        """Convert vector to HTML string representation for documentation."""

        str_ds = "\n       ".join(self.ds.__str__().split("\n"))

        # Over-ride Raster's method to remove nodata value (always None)
        # Use <pre> to keep white spaces, <span> to keep line breaks
        s = str(
            '<pre><span style="white-space: pre-wrap"><b><em>'
            + self.__class__.__name__
            + "</em></b>(\n"
            + "  <b>ds=</b>"
            + str_ds
            + "\n  <b>crs=</b>"
            + self.crs.__str__()
            + "\n  <b>bounds=</b>"
            + self.bounds.__repr__()
            + ")</span></pre>"
        )

        return s

    def __str__(self) -> str:
        """Provide simplified vector string representation for print()."""

        return str(self.ds.__str__())

    def __getitem__(self, value: gu.Raster | Vector | list[float] | tuple[float, ...]) -> Vector:
        """
        Index or subset the vector.
        """

        return self.crop(crop_geom=value, clip=False, inplace=False)

    def info(self) -> str:
        """
        Summarize information about the vector.

        :returns: text information about Vector attributes.
        :rtype: str
        """
        as_str = [  # 'Driver:             {} \n'.format(self.driver),
            f"Filename:           {self.name} \n",
            f"Coordinate System:  EPSG:{self.ds.crs.to_epsg()}\n",
            f"Number of features: {len(self.ds)} \n",
            f"Extent:             {self.ds.total_bounds.tolist()} \n",
            f"Attributes:         {self.ds.columns.tolist()} \n",
            self.ds.__repr__(),
        ]

        return "".join(as_str)

    def show(
        self,
        ref_crs: gu.Raster | rio.io.DatasetReader | VectorType | gpd.GeoDataFrame | str | CRS | int | None = None,
        cmap: matplotlib.colors.Colormap | str | None = None,
        vmin: float | int | None = None,
        vmax: float | int | None = None,
        alpha: float | int | None = None,
        cbar_title: str | None = None,
        add_cbar: bool = False,
        ax: matplotlib.axes.Axes | None | Literal["new"] = None,
        return_axes: bool = False,
        **kwargs: Any,
    ) -> None | tuple[matplotlib.axes.Axes, matplotlib.colors.Colormap]:
        r"""
        Plot the vector.

        This method is a wrapper to geopandas.GeoDataFrame.plot. Any \*\*kwargs which
        you give this method will be passed to it.

        :param ref_crs: The CRS to match when plotting.
        :param cmap: The colormap. Default is plt.rcParams['image.cmap'].
        :param vmin: Colorbar minimum value. Default is data min.
        :param vmax: Colorbar maximum value. Default is data max.
        :param alpha: Transparency of raster and colorbar.
        :param cbar_title: Colorbar label. Default is None.
        :param add_cbar: Set to True to display a colorbar. Default is True.
        :param ax: A figure ax to be used for plotting. If None, will plot on current axes. If "new",
            will create a new axis.
        :param return_axes: Whether to return axes.

        :returns: None, or (ax, caxes) if return_axes is True
        """

        # Ensure that the vector is in the same crs as a reference
        if isinstance(ref_crs, (gu.Raster, rio.io.DatasetReader, Vector, gpd.GeoDataFrame, str)):
            vect_reproj = self.reproject(dst_ref=ref_crs)
        elif isinstance(ref_crs, (CRS, int)):
            vect_reproj = self.reproject(dst_crs=ref_crs)
        else:
            vect_reproj = self

        # Create axes, or get current ones by default (like in matplotlib)
        if ax is None:
            # If no figure exists, get a new axis
            if len(plt.get_fignums()) == 0:
                ax0 = plt.gca()
            # Otherwise, get first axis
            else:
                ax0 = plt.gcf().axes[0]
        elif isinstance(ax, str) and ax.lower() == "new":
            _, ax0 = plt.subplots()
        elif isinstance(ax, matplotlib.axes.Axes):
            ax0 = ax
        else:
            raise ValueError("ax must be a matplotlib.axes.Axes instance, 'new' or None.")

        # Update with this function's arguments
        if add_cbar:
            legend = True
        else:
            legend = False

        if "legend" in list(kwargs.keys()):
            legend = kwargs.pop("legend")
        else:
            legend = False

        # Get colormap arguments that might have been passed in the keyword args
        if "legend_kwds" in list(kwargs.keys()) and legend:
            legend_kwds = kwargs.pop("legend_kwds")
            if "label" in list(legend_kwds):
                cbar_title = legend_kwds.pop("label")
        else:
            legend_kwds = None

        # Add colorbar
        if add_cbar or cbar_title:
            divider = make_axes_locatable(ax0)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)
            cbar = matplotlib.colorbar.ColorbarBase(
                cax, cmap=cmap, norm=norm
            )  # , orientation="horizontal", ticklocation="top")
            cbar.solids.set_alpha(alpha)

            if cbar_title is not None:
                cbar.set_label(cbar_title)
        else:
            cax = None
            cbar = None

        # Plot
        vect_reproj.ds.plot(
            ax=ax0,
            cax=cax,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            alpha=alpha,
            legend=legend,
            legend_kwds=legend_kwds,
            **kwargs,
        )

        # If returning axes
        if return_axes:
            return ax, cax
        else:
            return None

    @property
    def bounds(self) -> rio.coords.BoundingBox:
        """Bounding box of the vector."""
        return rio.coords.BoundingBox(*self.ds.total_bounds)

    @property
    def crs(self) -> rio.crs.CRS:
        """Coordinate reference system of the vector."""
        self._crs = self.ds.crs
        return self._crs

    @property
    def ds(self) -> gpd.GeoDataFrame:
        """Geodataframe of the vector."""
        return self._ds

    @ds.setter
    def ds(self, new_ds: gpd.GeoDataFrame | gpd.GeoSeries) -> None:
        """Set a new geodataframe."""

        if isinstance(new_ds, gpd.GeoDataFrame):
            self._ds = new_ds
        elif isinstance(new_ds, gpd.GeoSeries):
            self._ds = gpd.GeoDataFrame(geometry=new_ds)
        else:
            raise ValueError("The dataset of a vector must be set with a GeoSeries or a GeoDataFrame.")

    @property
    def name(self) -> str | None:
        """Name on disk, if it exists."""
        return self._name

    def copy(self: VectorType) -> VectorType:
        """Return a copy of the vector."""
        # Utilise the copy method of GeoPandas
        new_vector = self.__new__(type(self))
        new_vector.__init__(self.ds.copy())  # type: ignore
        return new_vector  # type: ignore

    @overload
    def crop(
        self: VectorType,
        crop_geom: gu.Raster | Vector | list[float] | tuple[float, ...],
        clip: bool,
        *,
        inplace: Literal[True],
    ) -> None:
        ...

    @overload
    def crop(
        self: VectorType,
        crop_geom: gu.Raster | Vector | list[float] | tuple[float, ...],
        clip: bool,
        *,
        inplace: Literal[False],
    ) -> VectorType:
        ...

    @overload
    def crop(
        self: VectorType,
        crop_geom: gu.Raster | Vector | list[float] | tuple[float, ...],
        clip: bool,
        *,
        inplace: bool = True,
    ) -> VectorType | None:
        ...

    def crop(
        self: VectorType,
        crop_geom: gu.Raster | Vector | list[float] | tuple[float, ...],
        clip: bool = False,
        inplace: bool = True,
    ) -> VectorType | None:
        """
        Crop the vector to given extent.

        Match-reference: a reference raster or vector can be passed to match bounds during cropping.

        Optionally, clip geometries to that extent (by default keeps all intersecting).

        Reprojection is done on the fly if georeferenced objects have different projections.

        :param crop_geom: Geometry to crop vector to, as either a Raster object, a Vector object, or a list of
            coordinates. If cropGeom is a Raster, crop() will crop to the boundary of the raster as returned by
            Raster.ds.bounds. If cropGeom is a Vector, crop() will crop to the bounding geometry. If cropGeom is a
            list of coordinates, the order is assumed to be [xmin, ymin, xmax, ymax].
        :param clip: Whether to clip the geometry to the given extent (by default keeps all intersecting).
        :param inplace: Update the vector inplace or return copy.
        """
        if isinstance(crop_geom, (gu.Raster, Vector)):
            # For another Vector or Raster, we reproject the bounding box in the same CRS as self
            xmin, ymin, xmax, ymax = crop_geom.get_bounds_projected(out_crs=self.crs)
        elif isinstance(crop_geom, (list, tuple)):
            xmin, ymin, xmax, ymax = crop_geom
        else:
            raise TypeError("Crop geometry must be a Raster, Vector, or list of coordinates.")

        # Need to separate the two options, inplace update
        if inplace:
            self._ds = self.ds.cx[xmin:xmax, ymin:ymax]
            if clip:
                self._ds = self.ds.clip(mask=(xmin, ymin, xmax, ymax))
            return None
        # Or create a copy otherwise
        else:
            new_vector = self.copy()
            new_vector._ds = new_vector.ds.cx[xmin:xmax, ymin:ymax]
            if clip:
                new_vector._ds = new_vector.ds.clip(mask=(xmin, ymin, xmax, ymax))
            return new_vector

    def reproject(
        self: Vector,
        dst_ref: gu.Raster | rio.io.DatasetReader | VectorType | gpd.GeoDataFrame | str | None = None,
        dst_crs: CRS | str | int | None = None,
    ) -> Vector:
        """
        Reproject vector to a specified coordinate reference system.

        Match-reference: a reference raster or vector can be passed to match CRS during reprojection.

        Alternatively, a CRS can be passed in many formats (string, EPSG integer, or CRS).

        To reproject a Vector with different source bounds, first run Vector.crop().

        :param dst_ref: A reference raster or vector whose CRS to use as a reference for reprojection.
            Can be provided as Raster, Vector, rasterio dataset, geopandas dataframe, or path to the file.
        :param dst_crs: Specify the Coordinate Reference System or EPSG to reproject to. If dst_ref not set,
            defaults to self.crs.

        :returns: Reprojected vector
        """

        # Check that either dst_ref or dst_crs is provided
        if (dst_ref is not None and dst_crs is not None) or (dst_ref is None and dst_crs is None):
            raise ValueError("Either of `dst_ref` or `dst_crs` must be set. Not both.")

        # Case a raster or vector is provided as reference
        if dst_ref is not None:

            # Check that dst_ref type is either str, Raster or rasterio data set
            # Preferably use Raster instance to avoid rasterio data set to remain open. See PR #45
            if isinstance(dst_ref, (gu.Raster, gu.Vector)):
                ds_ref = dst_ref
            elif isinstance(dst_ref, (rio.io.DatasetReader, gpd.GeoDataFrame)):
                ds_ref = dst_ref
            elif isinstance(dst_ref, str):
                if not os.path.exists(dst_ref):
                    raise ValueError("Reference raster or vector path does not exist.")
                try:
                    ds_ref = gu.Raster(dst_ref, load_data=False)
                except rasterio.errors.RasterioIOError:
                    try:
                        ds_ref = Vector(dst_ref)
                    except fiona.errors.DriverError:
                        raise ValueError("Could not open raster or vector with rasterio or fiona.")
            else:
                raise TypeError("Type of dst_ref must be string path to file, Raster or Vector.")

            # Read reprojecting params from ref raster
            dst_crs = ds_ref.crs
        else:
            # Determine user-input target CRS
            dst_crs = CRS.from_user_input(dst_crs)

        return Vector(self.ds.to_crs(crs=dst_crs))

    @overload
    def create_mask(
        self,
        rst: str | gu.Raster | None = None,
        crs: CRS | None = None,
        xres: float | None = None,
        yres: float | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        buffer: int | float | np.number = 0,
        *,
        as_array: Literal[False] = False,
    ) -> gu.Mask:
        ...

    @overload
    def create_mask(
        self,
        rst: str | gu.Raster | None = None,
        crs: CRS | None = None,
        xres: float | None = None,
        yres: float | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        buffer: int | float | np.number = 0,
        *,
        as_array: Literal[True],
    ) -> np.ndarray:
        ...

    def create_mask(
        self,
        rst: gu.Raster | None = None,
        crs: CRS | None = None,
        xres: float | None = None,
        yres: float | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        buffer: int | float | np.number = 0,
        as_array: bool = False,
    ) -> gu.Mask | np.ndarray:
        """
        Create a mask from the vector features.

        Match-reference: a raster can be passed to match its resolution, bounds and CRS when creating the mask.

        Alternatively, user can specify a grid to rasterize on using xres, yres, bounds and crs.
        Only xres is mandatory, by default yres=xres and bounds/crs are set to self's.

        Vector features which fall outside the bounds of the raster file are not written to the new mask file.

        :param rst: A raster
        :param crs: A pyproj or rasterio CRS object (Default to rst.crs if not None then self.crs)
        :param xres: Output raster spatial resolution in x. Only is rst is None.
        :param yres: Output raster spatial resolution in y. Only if rst is None. (Default to xres)
        :param bounds: Output raster bounds (left, bottom, right, top). Only if rst is None (Default to self bounds)
        :param buffer: Size of buffer to be added around the features, in the raster's projection units.
            If a negative value is set, will erode the features.
        :param as_array: Return mask as a boolean array

        :returns: A Mask object contain a boolean array
        """

        # If no rst given, use provided dimensions
        if rst is None:

            # At minimum, xres must be set
            if xres is None:
                raise ValueError("At least rst or xres must be set.")
            if yres is None:
                yres = xres

            # By default, use self's CRS and bounds
            if crs is None:
                crs = self.ds.crs
            if bounds is None:
                bounds_shp = True
                bounds = self.ds.total_bounds
            else:
                bounds_shp = False

            # Calculate raster shape
            left, bottom, right, top = bounds
            height = abs((right - left) / xres)
            width = abs((top - bottom) / yres)

            if width % 1 != 0 or height % 1 != 0:
                # Only warn if the bounds were provided, and not derived from the vector
                if not bounds_shp:
                    warnings.warn("Bounds not a multiple of xres/yres, use rounded bounds.")

            width = int(np.round(width))
            height = int(np.round(height))
            out_shape = (height, width)

            # Calculate raster transform
            transform = rio.transform.from_bounds(left, bottom, right, top, width, height)

        # otherwise use directly rst's dimensions
        elif isinstance(rst, gu.Raster):
            out_shape = rst.shape
            transform = rst.transform
            crs = rst.crs
            bounds = rst.bounds
        else:
            raise TypeError("Raster must be a geoutils.Raster or None.")

        # Copying GeoPandas dataframe before applying changes
        gdf = self.ds.copy()

        # Crop vector geometries to avoid issues when reprojecting
        left, bottom, right, top = bounds  # type: ignore
        x1, y1, x2, y2 = warp.transform_bounds(crs, gdf.crs, left, bottom, right, top)
        gdf = gdf.cx[x1:x2, y1:y2]

        # Reproject vector into rst CRS
        gdf = gdf.to_crs(crs)

        # Create a buffer around the features
        if not isinstance(buffer, (int, float, np.number)):
            raise TypeError(f"Buffer must be a number, currently set to {type(buffer).__name__}.")
        if buffer != 0:
            gdf.geometry = [geom.buffer(buffer) for geom in gdf.geometry]
        elif buffer == 0:
            pass

        # Rasterize geometry
        mask = features.rasterize(
            shapes=gdf.geometry, fill=0, out_shape=out_shape, transform=transform, default_value=1, dtype="uint8"
        ).astype("bool")

        # Force output mask to be of same dimension as input rst
        if rst is not None:
            mask = mask.reshape((rst.count, rst.height, rst.width))  # type: ignore

        # Return output as mask or as array
        if as_array:
            return mask.squeeze()
        else:
            return gu.Raster.from_array(data=mask, transform=transform, crs=crs, nodata=None)

    def rasterize(
        self,
        rst: gu.Raster | None = None,
        crs: CRS | int | None = None,
        xres: float | None = None,
        yres: float | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        in_value: int | float | abc.Iterable[int | float] | None = None,
        out_value: int | float = 0,
    ) -> gu.Raster | gu.Mask:
        """
        Rasterize vector to a raster or mask, with input geometries burned in.

        Match-reference: a raster can be passed to match its resolution, bounds and CRS when rasterizing the vector.
        Alternatively, user can specify a grid to rasterize on using xres, yres, bounds and crs.
        Only xres is mandatory, by default yres=xres and bounds/crs are set to self's.

        Burn value is set by user and can be either a single number, or an iterable of same length as self.ds.
        Default is an index from 1 to len(self.ds).

        :param rst: A raster to be used as reference for the output grid
        :param crs: A pyproj or rasterio CRS object (Default to rst.crs if not None then self.crs)
        :param xres: Output raster spatial resolution in x. Only if rst is None.
            Must be in units of crs, if set.
        :param yres: Output raster spatial resolution in y. Only if rst is None.
            Must be in units of crs, if set. (Default to xres)
        :param bounds: Output raster bounds (left, bottom, right, top). Only if rst is None
            Must be in same system as crs, if set. (Default to self bounds).
        :param in_value: Value(s) to be burned inside the polygons (Default is self.ds.index + 1)
        :param out_value: Value to be burned outside the polygons (Default is 0)

        :returns: Raster or mask containing the burned geometries
        """

        if (rst is not None) and (crs is not None):
            raise ValueError("Only one of rst or crs can be provided.")

        # Reproject vector into requested CRS or rst CRS first, if needed
        # This has to be done first so that width/height calculated below are correct!
        if crs is None:
            crs = self.ds.crs

        if rst is not None:
            crs = rst.crs  # type: ignore

        vect = self.ds.to_crs(crs)

        # If no rst given, now use provided dimensions
        if rst is None:

            # At minimum, xres must be set
            if xres is None:
                raise ValueError("At least rst or xres must be set.")
            if yres is None:
                yres = xres

            # By default, use self's bounds
            if bounds is None:
                bounds = vect.total_bounds

            # Calculate raster shape
            left, bottom, right, top = bounds
            width = abs((right - left) / xres)
            height = abs((top - bottom) / yres)

            if width % 1 != 0 or height % 1 != 0:
                warnings.warn("Bounds not a multiple of xres/yres, use rounded bounds.")

            width = int(np.round(width))
            height = int(np.round(height))
            out_shape = (height, width)

            # Calculate raster transform
            transform = rio.transform.from_bounds(left, bottom, right, top, width, height)

        # otherwise use directly rst's dimensions
        else:
            out_shape = rst.shape  # type: ignore
            transform = rst.transform  # type: ignore

        # Set default burn value, index from 1 to len(self.ds)
        if in_value is None:
            in_value = self.ds.index + 1

        # Rasterize geometry
        if isinstance(in_value, abc.Iterable):
            if len(in_value) != len(vect.geometry):  # type: ignore
                raise ValueError(
                    "in_value must have same length as self.ds.geometry, currently {} != {}".format(
                        len(in_value), len(vect.geometry)  # type: ignore
                    )
                )

            out_geom = ((geom, value) for geom, value in zip(vect.geometry, in_value))

            mask = features.rasterize(shapes=out_geom, fill=out_value, out_shape=out_shape, transform=transform)

        elif isinstance(in_value, Number):
            mask = features.rasterize(
                shapes=vect.geometry, fill=out_value, out_shape=out_shape, transform=transform, default_value=in_value
            )
        else:
            raise ValueError("in_value must be a single number or an iterable with same length as self.ds.geometry")

        # We return a mask if there is a single value to burn and this value is 1
        if isinstance(in_value, (int, np.integer, float, np.floating)) and in_value == 1:
            output = gu.Mask.from_array(data=mask, transform=transform, crs=crs, nodata=None)

        # Otherwise we return a Raster if there are several values to burn
        else:
            output = gu.Raster.from_array(data=mask, transform=transform, crs=crs, nodata=None)

        return output

    @classmethod
    def from_bounds_projected(
        cls, raster_or_vector: gu.Raster | VectorType, out_crs: CRS | None = None, densify_pts: int = 5000
    ) -> VectorType:
        """Create a vector polygon from projected bounds of a raster or vector.

        :param raster_or_vector: A raster or vector
        :param out_crs: In which CRS to compute the bounds
        :param densify_pts: Maximum points to be added between image corners to account for nonlinear edges.
        Reduce if time computation is really critical (ms) or increase if extent is not accurate enough.
        """

        if out_crs is None:
            out_crs = raster_or_vector.crs

        poly = gu.projtools.bounds2poly(raster_or_vector, out_crs=out_crs)

        df = gpd.GeoDataFrame(geometry=[poly], crs=out_crs)

        return cls(df)  # type: ignore

    def query(self: Vector, expression: str, inplace: bool = False) -> Vector | None:
        """
        Query the vector with a valid Pandas expression.

        :param expression: A python-like expression to evaluate. Example: "col1 > col2".
        :param inplace: Whether the query should modify the data in place or return a modified copy.

        :returns: Vector resulting from the provided query expression or itself if inplace=True.
        """
        # Modify inplace if wanted and return the self instance.
        if inplace:
            self._ds = self.ds.query(expression, inplace=True)
            return None

        # Otherwise, create a new Vector from the queried dataset.
        new_vector = Vector(self.ds.query(expression))
        return new_vector

    def proximity(
        self,
        raster: gu.Raster | None = None,
        grid_size: tuple[int, int] = (1000, 1000),
        geometry_type: str = "boundary",
        in_or_out: Literal["in"] | Literal["out"] | Literal["both"] = "both",
        distance_unit: Literal["pixel"] | Literal["georeferenced"] = "georeferenced",
    ) -> gu.Raster:
        """
        Compute proximity distances to this vector's geometry.

        Match-reference: a raster can be passed to match its resolution, bounds and CRS for computing
        proximity distances.

        Alternatively, a grid size can be passed to create a georeferenced grid with the bounds and CRS of this vector.

        By default, the boundary of the Vector's geometry will be used. The full geometry can be used by
        passing "geometry",
        or any lower dimensional geometry attribute such as "centroid", "envelope" or "convex_hull".
        See all geometry attributes in the Shapely documentation at https://shapely.readthedocs.io/.

        :param raster: Raster to burn the proximity grid on.
        :param grid_size: If no Raster is provided, grid size to use with this Vector's extent and CRS
            (defaults to 1000 x 1000).
        :param geometry_type: Type of geometry to use for the proximity, defaults to 'boundary'.
        :param in_or_out: Compute proximity only 'in' or 'out'-side the polygon, or 'both'.
        :param distance_unit: Distance unit, either 'georeferenced' or 'pixel'.

        :return: Proximity raster.
        """

        # 0/ If no Raster is passed, create one on the Vector bounds of size 1000 x 1000
        if raster is None:

            # TODO: this bit of code is common in several vector functions (rasterize, etc): move out as common code?
            # By default, use self's bounds
            if self.bounds is None:
                raise ValueError("To automatically rasterize on the vector, bounds need to be defined.")

            # Calculate raster shape
            left, bottom, right, top = self.bounds

            # Calculate raster transform
            transform = rio.transform.from_bounds(left, bottom, right, top, grid_size[0], grid_size[1])

            raster = gu.Raster.from_array(data=np.zeros((1000, 1000)), transform=transform, crs=self.crs)

        proximity = gu.raster.raster.proximity_from_vector_or_raster(
            raster=raster, vector=self, geometry_type=geometry_type, in_or_out=in_or_out, distance_unit=distance_unit
        )

        return raster.copy(new_array=proximity)

    def buffer_metric(self, buffer_size: float) -> Vector:
        """
        Buffer the vector features in a local metric system (UTM only).

        The outlines are projected to a local UTM, then reverted to the original projection after buffering.

        :param buffer_size: Buffering distance in meters

        :return: Buffered shapefile
        """

        from geoutils.projtools import latlon_to_utm, utm_to_epsg

        # Get a rough centroid in geographic coordinates (ignore the warning that it is not the most precise):
        with warnings.catch_warnings():
            warnings.simplefilter(action="ignore", category=UserWarning)
            shp_wgs84 = self.ds.to_crs(epsg=4326)
            lat, lon = shp_wgs84.centroid.y.values[0], shp_wgs84.centroid.x.values[0]
            del shp_wgs84

        # Get the EPSG code of the local UTM
        utm = latlon_to_utm(lat, lon)
        epsg = utm_to_epsg(utm)

        # Reproject the shapefile in the local UTM
        ds_utm = self.ds.to_crs(epsg=epsg)

        # Buffer the shapefile
        ds_buffered = ds_utm.buffer(distance=buffer_size)
        del ds_utm

        # Revert-project the shapefile in the original CRS
        ds_buffered_origproj = ds_buffered.to_crs(crs=self.ds.crs)
        del ds_buffered

        # Return a Vector object of the buffered GeoDataFrame
        # TODO: Clarify what is conserved in the GeoSeries and what to pass the GeoDataFrame to not lose any attributes
        vector_buffered = Vector(gpd.GeoDataFrame(geometry=ds_buffered_origproj.geometry, crs=self.ds.crs))

        return vector_buffered

    def get_bounds_projected(self, out_crs: CRS, densify_pts: int = 5000) -> rio.coords.BoundingBox:
        """
        Get vector bounds projected in a specified CRS.

        :param out_crs: Output CRS
        :param densify_pts: Maximum points to be added between image corners to account for nonlinear edges.
            Reduce if time computation is really critical (ms) or increase if extent is not accurate enough.
        """

        # Calculate new bounds
        new_bounds = _get_bounds_projected(self.bounds, in_crs=self.crs, out_crs=out_crs, densify_pts=densify_pts)

        return new_bounds

    def buffer_without_overlap(self, buffer_size: int | float, metric: bool = True, plot: bool = False) -> Vector:
        """
        Buffer the vector geometries without overlapping each other.

        The algorithm is based upon this tutorial: https://statnmap.com/2020-07-31-buffer-area-for-nearest-neighbour/.
        The buffered polygons are created using Voronoi polygons in order to delineate the "area of influence"
        of each geometry.
        The buffer is slightly inaccurate where two geometries touch, due to the nature of the Voronoi polygons,
        hence one geometry "steps" slightly on the neighbor buffer in some cases.
        The algorithm may also yield unexpected results on very simple geometries.

        Note: A similar functionality is provided by momepy (http://docs.momepy.org) and is probably more robust.
        It could be implemented in GeoPandas in the future: https://github.com/geopandas/geopandas/issues/2015.

        :param buffer_size: Buffer size in self's coordinate system units.
        :param metric: Whether to perform the buffering in a local metric system (default: True).
        :param plot: Set to True to show intermediate plots, useful for understanding or debugging.

        :returns: A Vector containing the buffered geometries.

        :examples: On glacier outlines.
            >>> outlines = gu.Vector(gu.examples.get_path('everest_rgi_outlines'))
            >>> outlines = gu.Vector(outlines.ds.to_crs('EPSG:32645'))
            >>> buffer = outlines.buffer_without_overlap(500)
            >>> ax = buffer.ds.plot()  # doctest: +SKIP
            >>> outlines.ds.plot(ax=ax, ec='k', fc='none')  # doctest: +SKIP
            >>> plt.show()  # doctest: +SKIP
        """

        from geoutils.projtools import latlon_to_utm, utm_to_epsg

        # Project in local UTM if metric is True
        if metric:
            # Get a rough centroid in geographic coordinates (ignore the warning that it is not the most precise):
            with warnings.catch_warnings():
                warnings.simplefilter(action="ignore", category=UserWarning)
                shp_wgs84 = self.ds.to_crs(epsg=4326)
                lat, lon = shp_wgs84.centroid.y.values[0], shp_wgs84.centroid.x.values[0]
                del shp_wgs84

            # Get the EPSG code of the local UTM
            utm = latlon_to_utm(lat, lon)
            epsg = utm_to_epsg(utm)

            gdf = self.ds.to_crs(epsg=epsg)
        else:
            gdf = self.ds

        # Dissolve all geometries into one
        merged = gdf.dissolve()

        # Add buffer around geometries
        merged_buffer = merged.buffer(buffer_size)

        # Extract only the buffered area
        buffer = merged_buffer.difference(merged)

        # Crop Voronoi polygons to bound geometry and add missing polygons
        bound_poly = bounds2poly(gdf)
        bound_poly = bound_poly.buffer(buffer_size)
        voronoi_all = generate_voronoi_with_bounds(gdf, bound_poly)
        if plot:
            plt.figure(figsize=(16, 4))
            ax1 = plt.subplot(141)
            voronoi_all.plot(ax=ax1)
            gdf.plot(fc="none", ec="k", ax=ax1)
            ax1.set_title("Voronoi polygons, cropped")

        # Extract Voronoi polygons only within the buffer area
        voronoi_diff = voronoi_all.intersection(buffer.geometry[0])

        # Split all polygons, and join attributes of original geometries into the Voronoi polygons
        # Splitting, i.e. explode, is needed when Voronoi generate MultiPolygons that may extend over several features.
        voronoi_gdf = gpd.GeoDataFrame(geometry=voronoi_diff.explode(index_parts=True))  # requires geopandas>=0.10
        joined_voronoi = gpd.tools.sjoin(gdf, voronoi_gdf, how="right")

        # Plot results -> some polygons are duplicated
        if plot:
            ax2 = plt.subplot(142, sharex=ax1, sharey=ax1)
            joined_voronoi.plot(ax=ax2, column="index_left", alpha=0.5, ec="k")
            gdf.plot(ax=ax2, column=gdf.index.values)
            ax2.set_title("Buffer with duplicated polygons")

        # Find non unique Voronoi polygons, and retain only first one
        _, indexes = np.unique(joined_voronoi.index, return_index=True)
        unique_voronoi = joined_voronoi.iloc[indexes]

        # Plot results -> unique polygons only
        if plot:
            ax3 = plt.subplot(143, sharex=ax1, sharey=ax1)
            unique_voronoi.plot(ax=ax3, column="index_left", alpha=0.5, ec="k")
            gdf.plot(ax=ax3, column=gdf.index.values)
            ax3.set_title("Buffer with unique polygons")

        # Dissolve all polygons by original index
        merged_voronoi = unique_voronoi.dissolve(by="index_left")

        # Plot
        if plot:
            ax4 = plt.subplot(144, sharex=ax1, sharey=ax1)
            gdf.plot(ax=ax4, column=gdf.index.values)
            merged_voronoi.plot(column=merged_voronoi.index.values, ax=ax4, alpha=0.5)
            ax4.set_title("Final buffer")
            plt.show()

        # Reverse-project to the original CRS if metric is True
        if metric:
            merged_voronoi = merged_voronoi.to_crs(crs=self.crs)

        return Vector(merged_voronoi)


# -----------------------------------------
# Additional stand-alone utility functions
# -----------------------------------------


def extract_vertices(gdf: gpd.GeoDataFrame) -> list[list[tuple[float, float]]]:
    r"""
    Function to extract the exterior vertices of all shapes within a gpd.GeoDataFrame.

    :param gdf: The GeoDataFrame from which the vertices need to be extracted.

    :returns: A list containing a list of (x, y) positions of the vertices. The length of the primary list is equal \
    to the number of geometries inside gdf, and length of each sublist is the number of vertices in the geometry.
    """
    vertices = []
    # Loop on all geometries within gdf
    for geom in gdf.geometry:
        # Extract geometry exterior(s)
        if geom.geom_type == "MultiPolygon":
            exteriors = [p.exterior for p in geom.geoms]
        elif geom.geom_type == "Polygon":
            exteriors = [geom.exterior]
        elif geom.geom_type == "LineString":
            exteriors = [geom]
        elif geom.geom_type == "MultiLineString":
            exteriors = list(geom.geoms)
        else:
            raise NotImplementedError(f"Geometry type {geom.geom_type} not implemented.")

        vertices.extend([list(ext.coords) for ext in exteriors])

    return vertices


def generate_voronoi_polygons(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Generate Voronoi polygons (tessellation) from the vertices of all geometries in a GeoDataFrame.

    Uses scipy.spatial.voronoi.

    :param: The GeoDataFrame from whose vertices are used for the Voronoi polygons.

    :returns: A GeoDataFrame containing the Voronoi polygons.
    """
    # Extract the coordinates of the vertices of all geometries in gdf
    vertices = extract_vertices(gdf)
    coords = np.concatenate(vertices)

    # Create the Voronoi diagram and extract ridges
    vor = Voronoi(coords)
    lines = [shapely.geometry.LineString(vor.vertices[line]) for line in vor.ridge_vertices if -1 not in line]
    polys = list(shapely.ops.polygonize(lines))
    if len(polys) == 0:
        raise ValueError("Invalid geometry, cannot generate finite Voronoi polygons")

    # Convert into GeoDataFrame
    voronoi = gpd.GeoDataFrame(geometry=gpd.GeoSeries(polys))
    voronoi.crs = gdf.crs

    return voronoi


def generate_voronoi_with_bounds(gdf: gpd.GeoDataFrame, bound_poly: Polygon) -> gpd.GeoDataFrame:
    """
    Generate Voronoi polygons that are bounded by the polygon bound_poly, to avoid Voronoi polygons that extend \
far beyond the original geometry.

    Voronoi polygons are created using generate_voronoi_polygons, cropped to the extent of bound_poly and gaps \
are filled with new polygons.

    :param: The GeoDataFrame from whose vertices are used for the Voronoi polygons.
    :param: A shapely Polygon to be used for bounding the Voronoi diagrams.

    :returns: A GeoDataFrame containing the Voronoi polygons.
    """
    # Create Voronoi polygons
    voronoi = generate_voronoi_polygons(gdf)

    # Crop Voronoi polygons to input bound_poly extent
    voronoi_crop = voronoi.intersection(bound_poly)
    voronoi_crop = gpd.GeoDataFrame(geometry=voronoi_crop)  # convert to DataFrame

    # Dissolve all Voronoi polygons and subtract from bounds to get gaps
    voronoi_merged = voronoi_crop.dissolve()
    bound_gdf = gpd.GeoDataFrame(geometry=gpd.GeoSeries(bound_poly))
    bound_gdf.crs = gdf.crs
    gaps = bound_gdf.difference(voronoi_merged)

    # Merge cropped Voronoi with gaps, if not empty, otherwise return cropped Voronoi
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Geometry is in a geographic CRS. Results from 'area' are likely incorrect.")
        tot_area = np.sum(gaps.area.values)

    if not tot_area == 0:
        voronoi_all = gpd.GeoDataFrame(geometry=list(voronoi_crop.geometry) + list(gaps.geometry))
        voronoi_all.crs = gdf.crs
        return voronoi_all
    else:
        return voronoi_crop
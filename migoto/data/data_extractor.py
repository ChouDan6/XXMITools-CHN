import collections
import copy
import numpy
from numpy.typing import NDArray, DTypeLike
import time
from bpy.types import Mesh, Object

from typing import Optional, Callable

from .byte_buffer import (
    AbstractSemantic,
    Semantic,
    BufferSemantic,
    NumpyBuffer,
    BufferLayout,
)
from .dxgi_format import DXGIFormat, DXGIType


class BlenderDataExtractor:
    blender_data_formats: dict[Semantic, DXGIFormat]
    blender_loop_semantics: list[Semantic] = [
        Semantic.Index,
        Semantic.VertexId,
        Semantic.Normal,
        Semantic.Tangent,
        Semantic.BitangentSign,
        Semantic.Color,
        Semantic.TexCoord,
    ]
    blender_vertex_semantics: list[Semantic] = [
        Semantic.Position,
        Semantic.Blendindices,
        Semantic.Blendweight,
    ]
    format_converters: dict[AbstractSemantic, list[Callable]] = {}
    semantic_converters: dict[AbstractSemantic, list[Callable]] = {}

    def get_data(
        self,
        mesh: Mesh,
        layout: BufferLayout,
        blender_data_formats: dict[Semantic, DXGIFormat],
        semantic_converters: dict[AbstractSemantic, list[Callable]],
        format_converters: dict[AbstractSemantic, list[Callable]],
        vertex_ids_cache: Optional[NDArray] = None,
        flip_winding=False,
    ) -> tuple[Optional[NDArray], NumpyBuffer]:
        self.blender_data_formats = blender_data_formats

        # Initialize converters
        for semantic, converter in self.semantic_converters.items():
            if semantic not in semantic_converters:
                semantic_converters[semantic] = converter
        for semantic, converter in self.format_converters.items():
            if semantic not in format_converters:
                format_converters[semantic] = converter

        layout.add_element(
            BufferSemantic(
                AbstractSemantic(Semantic.VertexId, 0),
                self.blender_data_formats[Semantic.VertexId],
            )
        )
        proxy_layout = self.make_proxy_layout(layout, semantic_converters)

        if vertex_ids_cache is None:
            # Extract requested data from blender loop vertices
            loop_data, index_data = self.get_loop_data(
                mesh, proxy_layout, flip_winding=flip_winding, dedupe=True
            )
            vertex_ids = loop_data.get_field(
                AbstractSemantic(Semantic.VertexId).get_name()
            )
        else:
            loop_data, index_data = None, None
            vertex_ids = vertex_ids_cache
            print("Skipped loop data fetching!")

        # Extract requested data from blender vertices
        vertex_data = self.get_vertex_data(mesh, proxy_layout)

        if vertex_data is not None:
            # Output vb is based on actual faces we're going to draw, so we need to make vertex_data match the loop_data
            # Multiple vertices from loop_data may refer the same one from vertex_data
            # Also, some vertices from vertex_data may end up not being required at all (when no faces use them)
            # Luckily, it can be done easily with numpy, and we can use vertex ids from loop_data as index for vertex_data
            vertex_data.set_data(vertex_data.get_data(vertex_ids))

        # Initialize vertex buffer with requested layout
        vertex_buffer = NumpyBuffer(layout, size=len(vertex_ids))

        # Convert received data and import it to output vertex buffer
        if loop_data is not None:
            vertex_buffer.import_data(loop_data, semantic_converters, format_converters)
        if vertex_data is not None:
            vertex_buffer.import_data(
                vertex_data, semantic_converters, format_converters
            )
        if index_data is not None:
            for index_converter in semantic_converters.get(
                AbstractSemantic(Semantic.Index), []
            ):
                index_data = index_converter(index_data)
            for index_converter in format_converters.get(
                AbstractSemantic(Semantic.Index), []
            ):
                index_data = index_converter(index_data)

        return index_data, vertex_buffer

    def make_proxy_layout(
        self,
        export_layout: BufferLayout,
        semantic_converters: dict[AbstractSemantic, list[Callable]],
    ) -> BufferLayout:
        # VertexId is required for export process, we should ensure its availability
        proxy_layout = BufferLayout([])
        # Some formats cannot be converted at foreach_get -> numpy request level and require special care
        for export_semantic in export_layout.semantics:
            blender_format: DXGIFormat = self.blender_data_formats[
                export_semantic.abstract.enum
            ]
            export_format: DXGIFormat = export_semantic.format

            proxy_semantic: BufferSemantic = copy.deepcopy(export_semantic)

            if export_semantic.extract_format is not None:
                # Export format has specified extraction format, lets hope they know what they're doing
                proxy_semantic.format = export_semantic.extract_format
                proxy_semantic.stride = export_semantic.extract_format.byte_width
            elif export_format.dxgi_type in [
                DXGIType.UNORM16,
                DXGIType.UNORM8,
                DXGIType.SNORM16,
                DXGIType.SNORM8,
            ]:
                # Formats UNORM16, UNORM8, SNORM16 and SNORM8 cannot be directly exported and require conversion
                proxy_semantic.format = blender_format
                proxy_semantic.stride = blender_format.byte_width
            elif export_semantic.abstract in semantic_converters.keys():
                # Semantic converter specified and it works with data values
                # Lets extract data in original format to prevent possible precision loss
                if export_semantic.abstract.enum in [
                    Semantic.Blendindices,
                    Semantic.Blendweight,
                ]:
                    proxy_semantic.stride = (
                        blender_format.byte_width * proxy_semantic.get_num_values()
                    )
                    proxy_semantic.format = blender_format
                else:
                    proxy_semantic.format = blender_format
                    proxy_semantic.stride = blender_format.byte_width
            elif export_semantic.abstract.enum not in [
                Semantic.Blendindices,
                Semantic.Blendweight,
            ]:
                # Only blends can be directly exported with any bitness and padding, because they aren't extracted with foreach_get
                # Other semantics may require conversion:
                if export_format.num_values != blender_format.num_values:
                    # Export formats with different number of values per row and cannot be filled by foreach_get directly
                    proxy_semantic.format = blender_format
                    proxy_semantic.stride = blender_format.byte_width
                elif export_format.value_byte_width > blender_format.value_byte_width:
                    # Export formats with more bits than blender storage and may corrupt data if used directly
                    proxy_semantic.format = blender_format
                    proxy_semantic.stride = blender_format.byte_width
                elif export_semantic.stride != blender_format.value_byte_width:
                    # Export format stride differs from the blender storage and cannot be filled by foreach_get directly
                    proxy_semantic.format = blender_format
                    proxy_semantic.stride = blender_format.byte_width

            proxy_layout.add_element(proxy_semantic)

        return proxy_layout

    def fetch_data(
        self, data_source, data_name: str, data_type: DTypeLike, size: int = 0
    ) -> NDArray:
        if size == 0:
            size = len(data_source)
        result = numpy.empty(size, dtype=data_type)
        data_source.foreach_get(data_name, result.ravel())
        return result

    def get_loop_data(
        self,
        mesh: Mesh,
        proxy_layout: BufferLayout,
        flip_winding=False,
        dedupe=False,
    ) -> tuple[NumpyBuffer, NDArray]:
        start_time: float = time.time()

        # Make loop data layout
        layout = BufferLayout([])
        for buffer_semantic in proxy_layout.semantics:
            if buffer_semantic.abstract.enum == Semantic.Index:
                continue
            if buffer_semantic.abstract.enum in self.blender_loop_semantics:
                layout.add_element(buffer_semantic)

        # Build triangle loop indices via vectorized fan triangulation.
        #
        # We use mesh.polygons.foreach_get("loop_total" / "loop_start") instead
        # of mesh.calc_loop_triangles() + foreach_get("loops") because the latter
        # is not guaranteed to write 3 values per triangle on all Blender versions /
        # mesh types, which caused quads to produce only 1 triangle instead of 2
        # (observed: draw count dropped from 5,896,860 to 3,932,751 on a 1M-vert
        # quad sphere ¡ª a 3:2 ratio matching poly-loop-count vs tri-loop-count).
        #
        # Fan triangulation: for a polygon with loop_start L and loop_total N,
        # produces N-2 triangles: (L, L+j+1, L+j+2) for j in 0..N-3.
        # This is identical to BMesh for convex polygons (all game meshes).
        n_polys: int = len(mesh.polygons)
        poly_loop_totals = numpy.empty(n_polys, dtype=numpy.int32)
        poly_loop_starts = numpy.empty(n_polys, dtype=numpy.int32)
        mesh.polygons.foreach_get("loop_total", poly_loop_totals)
        mesh.polygons.foreach_get("loop_start", poly_loop_starts)

        tri_counts = poly_loop_totals - 2          # triangles per polygon (N-2 for N-gon)
        n_tris: int = int(tri_counts.sum())

        # For each triangle, record which polygon it came from and its fan index j.
        poly_rep = numpy.repeat(
            numpy.arange(n_polys, dtype=numpy.int32), tri_counts
        )
        # tri_global_start[k] = index of the first triangle in poly_rep[k]'s polygon
        cumsum_tris = numpy.empty(n_polys, dtype=numpy.int32)
        numpy.cumsum(tri_counts[:-1], out=cumsum_tris[1:])
        cumsum_tris[0] = 0
        tri_global_start = numpy.repeat(cumsum_tris, tri_counts)
        local_j = numpy.arange(n_tris, dtype=numpy.int32) - tri_global_start

        starts = poly_loop_starts[poly_rep]
        tri_loop_indices = numpy.empty(n_tris * 3, dtype=numpy.int32)
        tri_loop_indices[0::3] = starts                 # fan apex (loop_start)
        tri_loop_indices[1::3] = starts + local_j + 1  # second vertex
        tri_loop_indices[2::3] = starts + local_j + 2  # third vertex

        # Apply winding flip to the index array instead of the data array;
        # semantically identical but avoids rewriting the whole data buffer.
        if flip_winding:
            tri_loop_indices = tri_loop_indices.reshape(-1, 3)
            tri_loop_indices[:, [0, 2]] = tri_loop_indices[:, [2, 0]]
            tri_loop_indices = tri_loop_indices.flatten()

        # Only compute tangents when the export layout actually needs them.
        # calc_tangents is an expensive Mikkt-space pass (~5-10 s for 1M-vert
        # meshes) and is wasted work when only Position / Blend / TexCoord are
        # being exported.
        needs_tangents: bool = any(
            s.abstract.enum in (Semantic.Tangent, Semantic.BitangentSign)
            for s in proxy_layout.semantics
        )
        if needs_tangents:
            mesh.calc_tangents(uvmap="TEXCOORD.xy")

        # Fetch loop data in polygon order (len(mesh.loops) entries).
        # We will reorder to triangle order afterwards with a single numpy
        # fancy-index step, which is O(N) and entirely in C.
        poly_size: int = len(mesh.loops)
        loop_data = NumpyBuffer(layout, size=poly_size)

        # Fetch data for requested semantics
        for buffer_semantic in proxy_layout.semantics:
            semantic: Semantic = buffer_semantic.abstract.enum
            semantic_name: str = buffer_semantic.get_name()
            numpy_type = buffer_semantic.get_numpy_type()
            if semantic == Semantic.VertexId:
                data = self.fetch_data(mesh.loops, "vertex_index", numpy_type, poly_size)
            elif semantic == Semantic.Normal:
                data = self.fetch_data(mesh.loops, "normal", numpy_type, poly_size)
            elif semantic == Semantic.Tangent:
                data = self.fetch_data(mesh.loops, "tangent", numpy_type, poly_size)
            elif semantic == Semantic.BitangentSign:
                data = self.fetch_data(mesh.loops, "bitangent_sign", numpy_type, poly_size)
            elif semantic == Semantic.Color:
                data = self.fetch_data(
                    mesh.vertex_colors[semantic_name].data, "color", numpy_type, poly_size
                )
            elif semantic == Semantic.TexCoord:
                data = self.fetch_data(
                    mesh.uv_layers[semantic_name].data, "uv", numpy_type, poly_size
                )
            else:
                continue
            self.sanitize_blender_data(data)
            loop_data.set_field(semantic_name, data)

        # Reorder from polygon order to triangle order in one numpy fancy-index
        # pass.  This replaces both the old flip_winding data-shuffle and the
        # dependency on the mesh already being triangulated by BMesh.
        loop_data.data = loop_data.data[tri_loop_indices]

        # Build IB and remove duplicate vertices in one vectorized pass
        index_data = None
        index_semantic = proxy_layout.get_element(AbstractSemantic(Semantic.Index))
        if index_semantic is not None or dedupe:
            # View each structured row as an opaque byte blob so numpy.unique can
            # compare whole rows without a Python-level loop.
            item_size = loop_data.data.itemsize
            void_view = loop_data.data.view(
                numpy.dtype((numpy.void, item_size))
            ).reshape(-1)
            # unique_idx  : position of each unique element's FIRST occurrence in void_view
            # inverse_idx : for every loop, which unique element it maps to (0-based into sorted uniques)
            _, unique_idx, inverse_idx = numpy.unique(
                void_view, return_index=True, return_inverse=True
            )
            # numpy.unique returns sorted uniques; restore first-occurrence order so
            # the output matches the original OrderedDict behaviour.
            first_order = numpy.argsort(unique_idx)          # sorted-unique -> first-occ order
            remap = numpy.empty(len(first_order), dtype=numpy.intp)
            remap[first_order] = numpy.arange(len(first_order))  # inverse mapping
            if index_semantic is not None:
                index_data = remap[inverse_idx].astype(index_semantic.get_numpy_type())
            if dedupe:
                loop_data.data = loop_data.data[unique_idx[first_order]]

        print(
            f"Loop data fetch time: {time.time() - start_time:.3f}s ({len(loop_data.get_data())} vertices, {len(index_data)} indices)"
        )

        return loop_data, index_data

    def get_vertex_data(self, mesh: Mesh, proxy_layout: BufferLayout) -> NumpyBuffer:
        start_time = time.time()

        # Make vertex data layout
        layout = BufferLayout([])
        for buffer_semantic in proxy_layout.semantics:
            if buffer_semantic.abstract.enum in self.blender_vertex_semantics:
                layout.add_element(buffer_semantic)

        if len(layout.semantics) == 0:
            print("Skipped vertex data fetching!")
            return None

        # Initialize vertex data storage
        size = len(mesh.vertices)
        vertex_data = NumpyBuffer(layout, size=size)

        # Determine whether blend data is needed for this layout
        needs_blend = any(
            s.abstract.enum in (Semantic.Blendindices, Semantic.Blendweight)
            for s in proxy_layout.semantics
        )

        # Flat COO-format vertex group arrays, built in a single Python pass.
        # Replaces N calls to sorted(vertex.groups, ...) with one traversal +
        # one numpy lexsort, then vectorized filling with num_vgs numpy passes.
        _blend_vi: numpy.ndarray = numpy.empty(0, dtype=numpy.int32)
        _blend_gi: numpy.ndarray = numpy.empty(0, dtype=numpy.int32)
        _blend_wt: numpy.ndarray = numpy.empty(0, dtype=numpy.float32)
        if needs_blend:
            vi_list: list = []
            gi_list: list = []
            wt_list: list = []
            for vi, vertex in enumerate(mesh.vertices):
                for vg in vertex.groups:
                    vi_list.append(vi)
                    gi_list.append(vg.group)
                    wt_list.append(vg.weight)
            if vi_list:
                _blend_vi = numpy.asarray(vi_list, dtype=numpy.int32)
                _blend_gi = numpy.asarray(gi_list, dtype=numpy.int32)
                _blend_wt = numpy.asarray(wt_list, dtype=numpy.float32)
                # Sort by vertex id (asc) then by weight (desc) with one C-level sort
                order = numpy.lexsort((-_blend_wt, _blend_vi))
                _blend_vi = _blend_vi[order]
                _blend_gi = _blend_gi[order]
                _blend_wt = _blend_wt[order]

        def _fill_blend(num_vgs: int, dtype: DTypeLike, src: numpy.ndarray) -> numpy.ndarray:
            """Vectorized fill: num_vgs numpy passes instead of size*num_vgs Python iterations."""
            out = numpy.zeros((size, num_vgs), dtype=dtype)
            if len(_blend_vi) == 0:
                return out
            starts = numpy.searchsorted(_blend_vi, numpy.arange(size), side="left")
            ends   = numpy.searchsorted(_blend_vi, numpy.arange(size), side="right")
            counts = numpy.minimum(ends - starts, num_vgs)
            for k in range(num_vgs):
                mask = counts > k
                out[mask, k] = src[starts[mask] + k]
            return out

        # Fetch data for requested semantics
        for buffer_semantic in proxy_layout.semantics:
            semantic: Semantic = buffer_semantic.abstract.enum
            numpy_type: DTypeLike = buffer_semantic.get_numpy_type()
            num_values: int = buffer_semantic.get_num_values()
            if semantic == Semantic.Position:
                data = self.fetch_data(mesh.vertices, "undeformed_co", numpy_type, size)
            elif semantic == Semantic.Blendindices:
                dtype: DTypeLike = (
                    numpy_type[0] if isinstance(numpy_type, tuple) else numpy_type
                )
                num_vgs: int = buffer_semantic.get_num_values()
                data = _fill_blend(num_vgs, dtype, _blend_gi)
            elif semantic == Semantic.Blendweight:
                dtype: DTypeLike = (
                    numpy_type[0] if isinstance(numpy_type, tuple) else numpy_type
                )
                num_vgs: int = buffer_semantic.get_num_values()
                data = _fill_blend(num_vgs, dtype, _blend_wt)
            else:
                continue
            self.sanitize_blender_data(data)
            if num_values == 1:
                data = data.reshape(-1)
            vertex_data.set_field(buffer_semantic.get_name(), data)

        print(
            f"Vertex data fetch time: {time.time() - start_time:.3f}s ({len(vertex_data.get_data())} vertices)"
        )

        return vertex_data

    def get_shapekey_data(
        self,
        obj: Object,
        names_filter: Optional[list[str]] = None,
        deduct_basis=False,
    ) -> dict[str, numpy.ndarray]:
        start_time = time.time()

        numpy_type = self.blender_data_formats[Semantic.ShapeKey].get_numpy_type()

        base_data = None
        if deduct_basis:
            base_data = self.fetch_data(
                obj.data.shape_keys.key_blocks["Basis"].data, "co", numpy_type
            )

        result = {}

        for shapekey in obj.data.shape_keys.key_blocks:
            if names_filter is not None:
                if shapekey.name not in names_filter:
                    continue
            elif deduct_basis and shapekey.name == "Basis":
                continue

            data = self.fetch_data(shapekey.data, "co", numpy_type)
            self.sanitize_blender_data(data)

            if deduct_basis:
                data -= base_data

            result[shapekey.name] = data

        print(
            f"Shape Keys fetch time: {time.time() - start_time:.3f}s ({len(result)} shapekeys)"
        )

        return result

    @staticmethod
    def sanitize_blender_data(arr: NDArray) -> None:
        """Sanitizes Blender data to prevent NaN values in the output."""
        if numpy.issubdtype(arr.dtype, numpy.floating):
            numpy.nan_to_num(arr, copy=False)

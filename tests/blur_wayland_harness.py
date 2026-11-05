"""A private, stateful Wayland compositor for blur protocol regression tests.

Only libwayland-client and libwayland-server are needed. No display socket,
GTK instance, desktop bus, compiler, or external compositor is involved.
"""

from __future__ import annotations

import ctypes as C
from ctypes.util import find_library
import socket
import threading
from types import SimpleNamespace

from zashterminal.utils.backdrop_blur import _Argument, _Interface, _Message


def native(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


def available():
    return bool(find_library("wayland-server") and find_library("wayland-client"))


class NativeSurface:
    def __init__(self, pointer, mapped=True):
        self.pointer = pointer
        self.mapped = mapped
        capsule = C.pythonapi.PyCapsule_New
        capsule.restype = C.py_object
        capsule.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p]
        self.__gpointer__ = capsule(pointer, None, None)

    def get_mapped(self):
        return self.mapped


class WaylandCompositor:
    """Serve actual protocol messages and retain compositor-visible state.

    Standard blur is double-buffered wl_surface state; KDE blur has its own
    commit. Regions are copied when assigned, just as the protocols require.
    Every callback, interface and message array stays rooted until disconnect.
    """

    def __init__(self, protocol="standard", capabilities=1, advertise_compositor=True,
                 refuse_manager=False):
        self.protocol = protocol
        self.capabilities = capabilities
        self.refuse_manager = refuse_manager
        self.errors = []
        self.resources = {}
        self.surfaces = {}
        self.effects = {}
        self.regions = {}
        self.retained = []
        self.stopped = threading.Event()
        self.server = C.CDLL(find_library("wayland-server"))
        self.client = C.CDLL(find_library("wayland-client"))
        self._configure_functions()
        self._define_protocol()
        self.server_display = self.display_create()
        if not self.server_display:
            raise RuntimeError("Could not create test Wayland display")
        self.loop = self.get_event_loop(self.server_display)
        self._bind_callback = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32, C.c_uint32)(self._bind)
        self._dispatch_callback = C.CFUNCTYPE(
            C.c_int, C.c_void_p, C.c_void_p, C.c_uint32,
            C.POINTER(_Message), C.POINTER(_Argument),
        )(self._dispatch)
        self._destroy_callback = C.CFUNCTYPE(None, C.c_void_p)(self._resource_destroyed)
        self.globals = []
        if advertise_compositor:
            self._global(self.compositor_interface, 4)
        if protocol in ("standard", "legacy", "both"):
            self._global(self.manager_interface, 1)
        if protocol == "both":
            self._global(self.legacy_manager_interface, 1)
        server_socket, client_socket = socket.socketpair()
        self.server_client = self.client_create(self.server_display, server_socket.detach())
        self.display = self.connect(client_socket.detach())
        if not self.server_client or not self.display:
            raise RuntimeError("Could not connect private Wayland socketpair")
        self.display_object = NativeSurface(self.display)
        identity = C.CFUNCTYPE(C.c_void_p, C.c_void_p)
        self.gtk = SimpleNamespace(
            gdk_wayland_display_get_wl_display=identity(lambda pointer: pointer),
            gdk_wayland_surface_get_wl_surface=identity(lambda pointer: pointer),
        )
        self.thread = threading.Thread(target=self._serve, name="blur-test-compositor", daemon=True)
        self.thread.start()
        self.compositor = None
        if advertise_compositor:
            self._bind_client_compositor()

    def _configure_functions(self):
        def server(name, result, *arguments):
            return native(self.server, name, result, *arguments)
        self.display_create = server("wl_display_create", C.c_void_p)
        self.display_destroy = server("wl_display_destroy", None, C.c_void_p)
        self.destroy_clients = server("wl_display_destroy_clients", None, C.c_void_p)
        self.get_event_loop = server("wl_display_get_event_loop", C.c_void_p, C.c_void_p)
        self.dispatch_loop = server("wl_event_loop_dispatch", C.c_int, C.c_void_p, C.c_int)
        self.flush_clients = server("wl_display_flush_clients", None, C.c_void_p)
        self.client_create = server("wl_client_create", C.c_void_p, C.c_void_p, C.c_int)
        self.global_create = server("wl_global_create", C.c_void_p, C.c_void_p,
                                    C.POINTER(_Interface), C.c_int, C.c_void_p, C.c_void_p)
        self.resource_create = server("wl_resource_create", C.c_void_p, C.c_void_p,
                                      C.POINTER(_Interface), C.c_int, C.c_uint32)
        self.set_dispatcher = server("wl_resource_set_dispatcher", None, C.c_void_p,
                                     C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)
        self.resource_destroy = server("wl_resource_destroy", None, C.c_void_p)
        self.get_client = server("wl_resource_get_client", C.c_void_p, C.c_void_p)
        self.post_event = server("wl_resource_post_event_array", None, C.c_void_p,
                                 C.c_uint32, C.POINTER(_Argument))
        self.post_error = server("wl_resource_post_error", None, C.c_void_p, C.c_uint32, C.c_char_p)
        self.connect = native(self.client, "wl_display_connect_to_fd", C.c_void_p, C.c_int)
        self.disconnect = native(self.client, "wl_display_disconnect", None, C.c_void_p)
        self.roundtrip = native(self.client, "wl_display_roundtrip", C.c_int, C.c_void_p)
        self.marshal = native(self.client, "wl_proxy_marshal_array_flags", C.c_void_p,
                              C.c_void_p, C.c_uint32, C.POINTER(_Interface), C.c_uint32,
                              C.c_uint32, C.POINTER(_Argument))
        self.proxy_destroy = native(self.client, "wl_proxy_destroy", None, C.c_void_p)
        self.proxy_id = native(self.client, "wl_proxy_get_id", C.c_uint32, C.c_void_p)
        self.add_listener = native(self.client, "wl_proxy_add_listener", C.c_int,
                                   C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p)

    def _define_protocol(self):
        self.compositor_interface = _Interface.in_dll(self.server, "wl_compositor_interface")
        self.surface_interface = _Interface.in_dll(self.server, "wl_surface_interface")
        self.region_interface = _Interface.in_dll(self.server, "wl_region_interface")
        self.registry_interface = _Interface.in_dll(self.client, "wl_registry_interface")
        surface_types = (C.POINTER(_Interface) * 1)(C.pointer(self.surface_interface))
        region_types = (C.POINTER(_Interface) * 1)(C.pointer(self.region_interface))
        modern_methods = (_Message * 2)(
            _Message(b"destroy", b"", None),
            _Message(b"set_blur_region", b"?o", region_types),
        )
        legacy_methods = (_Message * 3)(
            _Message(b"commit", b"", None), _Message(b"set_region", b"?o", region_types),
            _Message(b"release", b"", None),
        )
        self.effect_interface = _Interface(b"ext_background_effect_surface_v1", 1, 2, modern_methods, 0, None)
        self.legacy_effect_interface = _Interface(b"org_kde_kwin_blur", 1, 3, legacy_methods, 0, None)
        modern_types = (C.POINTER(_Interface) * 2)(C.pointer(self.effect_interface), C.pointer(self.surface_interface))
        legacy_types = (C.POINTER(_Interface) * 2)(C.pointer(self.legacy_effect_interface), C.pointer(self.surface_interface))
        manager_methods = (_Message * 2)(
            _Message(b"destroy", b"", None), _Message(b"get_background_effect", b"no", modern_types),
        )
        legacy_manager_methods = (_Message * 2)(
            _Message(b"create", b"no", legacy_types), _Message(b"unset", b"o", surface_types),
        )
        manager_events = (_Message * 1)(_Message(b"capabilities", b"u", None))
        self.modern_manager_interface = _Interface(
            b"ext_background_effect_manager_v1", 1, 2, manager_methods, 1, manager_events,
        )
        self.legacy_manager_interface = _Interface(
            b"org_kde_kwin_blur_manager", 1, 2, legacy_manager_methods, 0, None,
        )
        self.manager_interface = self.legacy_manager_interface if self.protocol == "legacy" else self.modern_manager_interface
        self.retained.extend((surface_types, region_types, modern_methods, legacy_methods,
                              modern_types, legacy_types, manager_methods, legacy_manager_methods, manager_events))

    def _global(self, interface, version):
        index = len(self.retained)
        self.retained.append(interface)
        self.globals.append(self.global_create(
            self.server_display, C.pointer(interface), version, index, self._bind_callback,
        ))

    def _resource(self, client, interface, version, identifier):
        resource = self.resource_create(client, C.pointer(interface), version, identifier)
        if not resource:
            raise RuntimeError("Compositor resource allocation failed")
        self.resources[resource] = interface.name
        self.set_dispatcher(resource, self._dispatch_callback, None, None, self._destroy_callback)
        return resource

    def _bind(self, client, index, version, identifier):
        try:
            interface = self.retained[index or 0]
            resource = self._resource(client, interface, version, identifier)
            if interface.name == b"ext_background_effect_manager_v1":
                if self.refuse_manager:
                    self.post_error(resource, 0, b"Test compositor refuses blur")
                else:
                    self.post_event(resource, 0, (_Argument * 1)(_Argument(u=self.capabilities)))
        except BaseException as error:
            self.errors.append(error)

    def _resource_destroyed(self, resource):
        self.resources.pop(resource, None)
        self.regions.pop(resource, None)
        self.effects.pop(resource, None)

    def _dispatch(self, implementation, resource, opcode, message, arguments):
        try:
            kind = self.resources[resource]
            client = self.get_client(resource)
            if kind == b"wl_compositor":
                interface = self.surface_interface if opcode == 0 else self.region_interface
                new = self._resource(client, interface, 4 if opcode == 0 else 1, arguments[0].u)
                if opcode == 0:
                    self.surfaces[new] = dict(pending=None, visible=None, commits=0,
                                              size=(80, 24), scale=1, buffer_attaches=0)
                else:
                    self.regions[new] = []
            elif kind == b"wl_region":
                if opcode == 0:
                    self.resource_destroy(resource)
                elif opcode == 1:
                    self.regions[resource].append(tuple(C.c_int32(arguments[i].u).value for i in range(4)))
                else:
                    raise AssertionError("Unexpected region subtraction")
            elif kind == b"wl_surface":
                state = self.surfaces[resource]
                if opcode == 0:
                    self.resource_destroy(resource)
                elif opcode == 1:
                    state["buffer_attaches"] += 1
                elif opcode == 6:
                    state["visible"] = state["pending"]
                    state["commits"] += 1
                elif opcode == 8:
                    state["scale"] = arguments[0].u
                else:
                    raise AssertionError("Unexpected wl_surface request: %s" % opcode)
            elif kind in (b"ext_background_effect_manager_v1", b"org_kde_kwin_blur_manager"):
                modern = kind == b"ext_background_effect_manager_v1"
                if modern and opcode == 0:
                    self.resource_destroy(resource)
                elif (modern and opcode == 1) or (not modern and opcode == 0):
                    surface = arguments[1].o
                    interface = self.effect_interface if modern else self.legacy_effect_interface
                    effect = self._resource(client, interface, 1, arguments[0].u)
                    self.effects[effect] = dict(surface=surface, region=None, modern=modern)
                else:
                    self.surfaces[arguments[0].o]["visible"] = None
                    self.surfaces[arguments[0].o]["pending"] = None
            else:
                effect = self.effects[resource]
                state = self.surfaces[effect["surface"]]
                if opcode == 1:
                    region = arguments[0].o
                    effect["region"] = tuple(self.regions[region]) if region else "whole-surface"
                    if effect["modern"]:
                        state["pending"] = effect["region"] if region else None
                elif effect["modern"]:
                    state["pending"] = None
                    self.resource_destroy(resource)
                elif opcode == 0:
                    state["visible"] = effect["region"]
                    state["pending"] = effect["region"]
                else:
                    self.resource_destroy(resource)
            return 0
        except BaseException as error:
            self.errors.append(error)
            self.post_error(resource, 0, b"Unexpected request in test compositor")
            return -1

    def _serve(self):
        while not self.stopped.is_set():
            self.dispatch_loop(self.loop, 10)
            self.flush_clients(self.server_display)

    def request(self, proxy, opcode, *arguments, interface=None, version=1, destroy=False):
        values = (_Argument * len(arguments))(*arguments) if arguments else None
        return self.marshal(proxy, opcode, C.pointer(interface) if interface is not None else None,
                            version, int(destroy), values)

    def sync(self):
        if self.roundtrip(self.display) < 0:
            raise RuntimeError("Private Wayland connection failed")
        if self.errors:
            raise AssertionError(self.errors)

    def _bind_client_compositor(self):
        registry = self.request(self.display, 1, _Argument(o=None), interface=self.registry_interface)
        names = {}
        global_type = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32, C.c_char_p, C.c_uint32)
        remove_type = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32)
        on_global = global_type(lambda data, proxy, name, interface, version: names.update({interface: name}))
        on_remove = remove_type(lambda data, proxy, name: None)
        listeners = (C.c_void_p * 2)(C.cast(on_global, C.c_void_p), C.cast(on_remove, C.c_void_p))
        self.retained.extend((on_global, on_remove, listeners))
        if self.add_listener(registry, listeners, None) != 0:
            raise RuntimeError("Could not listen to test registry")
        self.sync()
        self.compositor = self.request(
            registry, 0, _Argument(u=names[b"wl_compositor"]), _Argument(s=b"wl_compositor"),
            _Argument(u=4), _Argument(o=None), interface=self.compositor_interface, version=4,
        )
        self.proxy_destroy(registry)
        self.sync()

    def surface(self, mapped=True):
        pointer = self.request(self.compositor, 0, _Argument(o=None), interface=self.surface_interface, version=4)
        self.sync()
        return NativeSurface(pointer, mapped)

    def state(self, surface):
        identifier = self.proxy_id(surface.pointer)
        # Client and server pointers differ; resource IDs are connection-local.
        get_id = native(self.server, "wl_resource_get_id", C.c_uint32, C.c_void_p)
        return next(state for resource, state in self.surfaces.items() if get_id(resource) == identifier)

    def commit(self, surface):
        self.request(surface.pointer, 6)
        self.sync()

    def resize(self, surface, width, height, scale):
        self.state(surface)["size"] = (width, height)
        self.request(surface.pointer, 8, _Argument(u=scale), version=4)
        self.commit(surface)

    def covers_surface(self, surface):
        state = self.state(surface)
        region = state["visible"]
        if region == "whole-surface":
            return True
        if not region:
            return False
        width, height = state["size"]
        return any(x <= 0 and y <= 0 and x + w >= width and y + h >= height for x, y, w, h in region)

    def live(self, interface):
        return sum(name == interface for name in self.resources.values())

    def drop_connection(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("Test compositor did not stop")
        self.destroy_clients(self.server_display)
        self.server_client = None

    def close(self):
        if self.server_display is None:
            return
        self.disconnect(self.display)
        self.drop_connection()
        self.display_destroy(self.server_display)
        self.server_display = None
        if self.resources:
            raise AssertionError("Native server resources survived disconnect: %r" % self.resources)
        if self.errors:
            raise AssertionError(self.errors)

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

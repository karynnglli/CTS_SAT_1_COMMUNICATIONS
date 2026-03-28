"""
CSP Routing Layer

This module implements static routing for CSP packets with manual interface
registration. No config files or dynamic discovery - all routes are configured
programmatically.

Usage:
    # Create route table
    route = Route(local_addr=10)

    # Add interface
    route.add_interface(tcptun, "tcptun0")

    # Add static route
    route.add_route(dst_addr=20, iface="tcptun0")

    # Set default route
    route.set_default_route("tcptun0")

    # Send packet
    route.send_packet(pkt)
"""

import logging
from typing import Dict, Tuple, Optional

from .packet import Packet
from .link import Interface

# Setup logging
logger = logging.getLogger(__name__)

# Global route table instance
_route = None


class Route:
    """
    CSP routing table with manual interface registration.

    Routes are stored as static mappings from CSP address to (interface, via_addr).
    No config files or dynamic discovery - all routes are configured programmatically.
    """

    def __init__(self, local_addr=0):
        """
        Initialize routing table.

        Args:
            local_addr: Local CSP address for this node
        """
        self.local_addr = local_addr

        # Route table: csp_addr → (interface, via_addr)
        self.routes: Dict[int, Tuple[Interface, int]] = {}

        # Interface registry: name → interface
        self.interfaces: Dict[str, Interface] = {}

        # Default route
        self.default_route: Optional[Tuple[Interface, int]] = None

        logger.info(f"Route table initialized (local_addr={local_addr})")

    def add_interface(self, iface, name=None):
        """
        Register an interface in the routing table.

        Args:
            iface: Interface object
            name: Interface name (uses iface.name if not specified)

        Returns:
            str: Interface name
        """
        if not isinstance(iface, Interface):
            raise TypeError("Interface must inherit from Interface base class")

        if name is None:
            if hasattr(iface, 'name'):
                name = iface.name
            else:
                raise ValueError("Interface name not specified and iface has no name attribute")

        self.interfaces[name] = iface
        logger.info(f"Added interface: {name}")

        return name

    def add_route(self, dst_addr, iface, via=0):
        """
        Add a static route to the routing table.

        Args:
            dst_addr: Destination CSP address
            iface: Interface object or interface name (str)
            via: Via address (0 = direct, non-zero = gateway)

        Returns:
            bool: True on success
        """
        # Resolve interface
        if isinstance(iface, str):
            if iface not in self.interfaces:
                logger.error(f"Interface not found: {iface}")
                return False
            interface = self.interfaces[iface]
        elif isinstance(iface, Interface):
            interface = iface
        else:
            raise TypeError("iface must be Interface object or string name")

        # Add route
        self.routes[dst_addr] = (interface, via)
        logger.info(f"Added route: {dst_addr} → {interface.name if hasattr(interface, 'name') else 'unknown'} (via={via})")

        return True

    def set_default_route(self, iface, via=0):
        """
        Set the default route for unknown destinations.

        Args:
            iface: Interface object or interface name (str)
            via: Via address (0 = direct, non-zero = gateway)

        Returns:
            bool: True on success
        """
        # Resolve interface
        if isinstance(iface, str):
            if iface not in self.interfaces:
                logger.error(f"Interface not found: {iface}")
                return False
            interface = self.interfaces[iface]
        elif isinstance(iface, Interface):
            interface = iface
        else:
            raise TypeError("iface must be Interface object or string name")

        self.default_route = (interface, via)
        logger.info(f"Set default route: {interface.name if hasattr(interface, 'name') else 'unknown'} (via={via})")

        return True

    def lookup(self, dst_addr):
        """
        Lookup route for destination address.

        Priority:
            1. Exact match in route table
            2. Default route
            3. None (no route found)

        Args:
            dst_addr: Destination CSP address

        Returns:
            tuple: (interface, via_addr) or None if no route found
        """
        # Check for exact match
        if dst_addr in self.routes:
            return self.routes[dst_addr]

        # Check for default route
        if self.default_route:
            return self.default_route

        # No route found
        logger.warning(f"No route to destination: {dst_addr}")
        return None

    def send_packet(self, pkt):
        """
        Route and send a packet.

        Args:
            pkt: Packet object to send

        Returns:
            bool: True on success, False on failure
        """
        if not isinstance(pkt, Packet):
            logger.error("Invalid packet type")
            return False

        # Extract destination address
        dst_addr = pkt.header.dst

        # Lookup route
        route = self.lookup(dst_addr)
        if route is None:
            logger.error(f"No route to destination: {dst_addr}")
            return False

        interface, _via = route

        # Send via interface
        try:
            success = interface.send(pkt)
            if success:
                logger.debug(f"Sent packet to {dst_addr} via {interface.name if hasattr(interface, 'name') else 'unknown'}")
            else:
                logger.error(f"Failed to send packet to {dst_addr}")
            return success
        except Exception as e:
            logger.error(f"Error sending packet: {e}")
            return False

    def get_local_addr(self):
        """
        Get the local CSP address.

        Returns:
            int: Local CSP address
        """
        return self.local_addr

    def set_local_addr(self, addr):
        """
        Set the local CSP address.

        Args:
            addr: New local CSP address
        """
        self.local_addr = addr
        logger.info(f"Set local address: {addr}")

    def get_interface(self, name):
        """
        Get interface by name.

        Args:
            name: Interface name

        Returns:
            Interface: Interface object or None if not found
        """
        return self.interfaces.get(name)

    def list_interfaces(self):
        """
        List all registered interfaces.

        Returns:
            list: List of interface names
        """
        return list(self.interfaces.keys())

    def list_routes(self):
        """
        List all routes.

        Returns:
            dict: Route table (dst_addr → (interface_name, via))
        """
        routes = {}
        for dst_addr, (iface, via) in self.routes.items():
            iface_name = iface.name if hasattr(iface, 'name') else 'unknown'
            routes[dst_addr] = (iface_name, via)
        return routes

    def clear_routes(self):
        """Clear all routes (except default route)."""
        self.routes.clear()
        logger.info("Cleared all routes")

    def clear_default_route(self):
        """Clear the default route."""
        self.default_route = None
        logger.info("Cleared default route")

    def __repr__(self):
        """String representation."""
        num_routes = len(self.routes)
        num_ifaces = len(self.interfaces)
        has_default = self.default_route is not None
        return f"Route(local_addr={self.local_addr}, routes={num_routes}, ifaces={num_ifaces}, default={has_default})"


# Global helper functions (legacy API)

def add_interface(iface, name=None):
    """
    Add interface to global route table.

    Args:
        iface: Interface object
        name: Interface name (optional)

    Returns:
        str: Interface name
    """
    global _route
    if _route is None:
        _route = Route(local_addr=0)
    return _route.add_interface(iface, name)


def connect(local_addr=None):
    """
    Initialize global route table.

    Args:
        local_addr: Local CSP address (default: 0)

    Returns:
        Route: Global route table instance
    """
    global _route
    if local_addr is None:
        local_addr = 0
    _route = Route(local_addr)
    return _route


def get_route():
    """
    Get the global route table instance.

    Returns:
        Route: Global route table or None if not initialized
    """
    return _route

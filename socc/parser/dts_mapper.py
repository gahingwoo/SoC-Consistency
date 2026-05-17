"""Map a DTS nested-dict tree to the SoC data model."""

from typing import Dict, Any, Optional, List
from socc.model import (
    SoC,
    Regulator,
    Clock,
    ClockProvider,
    PowerTree,
    ClockTree,
    IRNode,
    ThermalZone,
    ThermalTrip,
)


def _get_int_prop(props: Dict[str, Any], key: str, default: int = 0) -> int:
    """Extract an integer property value from a props dict, handling list wrapping."""
    val = props.get(key, default)
    if isinstance(val, list) and val:
        val = val[0]
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


class DTSMapper:
    """Maps a DTS tree to the SoC data model."""
    
    def __init__(self, dts_tree: Dict[str, Any], soc_name: str = "unknown"):
        self.dts_tree = dts_tree
        self.soc_name = soc_name
        self.phandle_map: Dict[str, Dict[str, Any]] = {}  # phandle -> node
        self.power_tree = PowerTree()
        self.clock_tree = ClockTree()
        self.devices: Dict[str, IRNode] = {}
        self.device_supplies: Dict[str, List[str]] = {}
        self.device_clocks: Dict[str, List[str]] = {}
    
    def map(self) -> SoC:
        """Map the DTS tree to a SoC model."""
        # pass 1: collect all nodes and build phandle map
        self._collect_phandles(self.dts_tree)
        
        # pass 2: extract power domains and regulators
        self._extract_power_nodes()
        
        # pass 3: extract clock providers and clocks
        self._extract_clock_nodes()
        
        # pass 4: extract device nodes
        self._extract_devices()

        # pass 5: extract pinmux configuration
        pinmux_config = self._extract_pinmux()

        # pass 6: extract thermal zones
        thermal_zones = self._extract_thermal_zones()
        
        # build SoC object
        soc = SoC(
            name=self.soc_name,
            power_tree=self.power_tree,
            clock_tree=self.clock_tree,
            devices=self.devices,
            device_supplies=self.device_supplies,
            device_clocks=self.device_clocks,
            pinmux_config=pinmux_config,
            thermal_zones=thermal_zones,
        )
        
        return soc

    def _extract_pinmux(self) -> Dict[str, str]:
        """Walk the DTS tree for pinmux/pinctrl groups and return {pin: function}."""
        pinmux: Dict[str, str] = {}

        def traverse(node: Dict[str, Any]) -> None:
            node_name = node.get("name", "")
            props = node.get("properties", {})
            # Detect pinctrl / iomux groups by common naming patterns
            if any(x in node_name.lower() for x in ["pinctrl", "iomux", "pmx", "pin-"]):
                # Extract pins property and function-value if present
                pins = props.get("pins") or props.get("rockchip,pins") or []
                function = props.get("function") or props.get("rockchip,function") or node_name
                if not isinstance(pins, list):
                    pins = [pins] if pins else []
                for pin in pins:
                    if isinstance(pin, str):
                        pinmux[pin] = str(function)
            # Also detect individual pin-N or pin@addr nodes
            if node_name.startswith("pin") and "function" in props:
                pin_id = props.get("pin", node_name)
                pinmux[str(pin_id)] = str(props["function"])
            for child in node.get("children", []):
                traverse(child)

        traverse(self.dts_tree)
        return pinmux

    def _extract_thermal_zones(self) -> Dict[str, "ThermalZone"]:
        """Walk the DTS tree for thermal-zones and extract trip points."""
        zones: Dict[str, ThermalZone] = {}

        def _parse_zone(zone_node: Dict[str, Any]) -> None:
            zone_name = zone_node.get("name", "unknown-thermal")
            props = zone_node.get("properties", {})
            zone = ThermalZone(
                name=zone_name,
                polling_delay=_get_int_prop(props, "polling-delay", 1_000),
                polling_delay_passive=_get_int_prop(props, "polling-delay-passive", 250),
            )
            # scan children for trips{} and cooling-maps{}
            for child in zone_node.get("children", []):
                child_name = child.get("name", "")
                if child_name == "trips":
                    for trip_node in child.get("children", []):
                        tprops = trip_node.get("properties", {})
                        temp_mc = _get_int_prop(tprops, "temperature", 0)
                        hyst_mc = _get_int_prop(tprops, "hysteresis", 2_000)
                        ttype = tprops.get("type", "passive")
                        if isinstance(ttype, list):
                            ttype = ttype[0] if ttype else "passive"
                        zone.trips.append(ThermalTrip(
                            name=trip_node.get("name", "trip"),
                            trip_type=str(ttype),
                            temperature=temp_mc,
                            hysteresis=hyst_mc,
                        ))
                elif child_name == "cooling-maps":
                    for cmap in child.get("children", []):
                        cprops = cmap.get("properties", {})
                        dev_ref = cprops.get("cooling-device", "")
                        if dev_ref:
                            zone.cooling_devices.append(str(dev_ref))
            zones[zone_name] = zone

        def traverse(node: Dict[str, Any]) -> None:
            name = node.get("name", "")
            if name == "thermal-zones":
                for zone_node in node.get("children", []):
                    _parse_zone(zone_node)
                return  # don't descend further inside thermal-zones
            for child in node.get("children", []):
                traverse(child)

        traverse(self.dts_tree)
        return zones

    def _collect_phandles(self, node: Dict[str, Any], path: str = "/") -> None:
        """Walk the DTS tree to build the phandle -> node map."""
        if node.get("type") == "node":
            node_name = node.get("name", "")
            label = node.get("label")
            
            # use label as phandle key
            if label:
                self.phandle_map[f"&{label}"] = node
        
        # recurse into children
        for child in node.get("children", []):
            self._collect_phandles(child, path)
    
    def _extract_power_nodes(self) -> None:
        """Extract power domain and regulator nodes from the DTS tree."""
        def traverse(node: Dict[str, Any]) -> None:
            node_type = node.get("type")
            if node_type not in ("node", "root"):
                return
            
            node_name = node.get("name", "")
            props = node.get("properties", {})
            
            # Detect regulator nodes by name patterns or regulator-* properties
            is_regulator = (
                "regulator" in node_name.lower() or
                "vdd" in node_name.lower() or
                "ldo" in node_name.lower() or
                "buck" in node_name.lower() or
                "dcdc" in node_name.lower() or
                "pmic" in node_name.lower() or
                any(k.startswith("regulator-") for k in props.keys())
            )
            
            if is_regulator and node_type == "node":  # skip root
                self._extract_regulator(node)
            
            # Detect power-domain nodes
            if "power" in node_name.lower() and "domain" in node_name.lower():
                self._extract_power_domain(node)
            
            # recurse
            for child in node.get("children", []):
                traverse(child)
        
        traverse(self.dts_tree)
    
    def _extract_regulator(self, node: Dict[str, Any]) -> None:
        """Extract a single regulator from a DTS node."""
        node_name = node.get("name", "regulator")
        label = node.get("label", node_name)
        props = node.get("properties", {})
        
        # prefer label as the regulator name
        actual_name = label if label else node_name
        
        # extract voltage range
        voltage_min = 3.3
        voltage_max = 3.3
        
        # look for microvolt properties
        if "regulator-min-microvolt" in props:
            min_uv = props.get("regulator-min-microvolt", 3300000)
            # value may be a list or scalar
            if isinstance(min_uv, list) and len(min_uv) > 0:
                min_uv = min_uv[0]
            voltage_min = float(min_uv) / 1_000_000
        
        if "regulator-max-microvolt" in props:
            max_uv = props.get("regulator-max-microvolt", 3300000)
            if isinstance(max_uv, list) and len(max_uv) > 0:
                max_uv = max_uv[0]
            voltage_max = float(max_uv) / 1_000_000
        
        # detect regulator type from node name
        reg_type = "unknown"
        name_lower = node_name.lower()
        if "fixed" in name_lower:
            reg_type = "fixed"
        elif "ldo" in name_lower:
            reg_type = "ldo"
        elif "buck" in name_lower or "dcdc" in name_lower:
            reg_type = "buck"
        elif "pmic" in name_lower:
            reg_type = "pmic"
        
        reg = Regulator(
            name=actual_name,
            type=reg_type,
            voltage_min=voltage_min,
            voltage_max=voltage_max,
            startup_delay_us=_get_int_prop(props, "regulator-enable-ramp-delay", 0),
            ramp_delay_us=_get_int_prop(props, "regulator-ramp-delay", 0),
        )
        
        try:
            self.power_tree.add_regulator(reg)
        except ValueError:
            # already exists, skip
            pass
    
    def _extract_power_domain(self, node: Dict[str, Any]) -> None:
        """Extract a power domain (modelled as a virtual regulator)."""
        node_name = node.get("name", "pd")
        
        # model power domain as a virtual regulator
        pd = Regulator(
            name=node_name,
            type="power_domain",
            voltage_min=1.0,
            voltage_max=3.3,
        )
        
        try:
            self.power_tree.add_regulator(pd)
        except ValueError:
            pass
    
    def _extract_clock_nodes(self) -> None:
        """Extract clock providers and clocks from the DTS tree."""
        def traverse(node: Dict[str, Any]) -> None:
            if node.get("type") not in ("node", "root"):
                return
            
            node_name = node.get("name", "")
            
            # detect clock providers by node name
            if any(x in node_name.lower() for x in ["clock", "pll", "osc", "xtal"]):
                self._extract_clock_provider(node)
            
            # recurse
            for child in node.get("children", []):
                traverse(child)
        
        traverse(self.dts_tree)
    
    def _extract_clock_provider(self, node: Dict[str, Any]) -> None:
        """Extract a clock provider from a DTS node."""
        node_name = node.get("name", "clock")
        label = node.get("label", node_name)
        props = node.get("properties", {})
        
        # infer provider type from node name
        name_lower = node_name.lower()
        if "pll" in name_lower:
            provider_type = "pll"
        elif "cru" in name_lower or "clock-controller" in name_lower:
            provider_type = "cru"
        elif "osc" in name_lower or "xtal" in name_lower:
            provider_type = "fixed"
        else:
            provider_type = "gate"

        provider = ClockProvider(
            name=label,
            type=provider_type,
        )
        
        try:
            self.clock_tree.add_provider(provider)
        except ValueError:
            pass
        
        # extract clock outputs from child nodes
        children = node.get("children", [])
        for child in children:
            child_name = child.get("name", "")
            if "clock" in child_name.lower() or "clk" in child_name.lower():
                self._extract_clock_from_provider(provider, child)
    
    def _extract_clock_from_provider(self, provider: ClockProvider, node: Dict[str, Any]) -> None:
        """Extract a clock from a provider child node."""
        clock_name = node.get("name", "clock")
        
        clock = Clock(
            name=clock_name,
            provider=provider.name,
            rate=0,
        )

        # extract frequency from clock-frequency property
        props = node.get("properties", {})
        if "clock-frequency" in props:
            freq_val = props.get("clock-frequency", 0)
            if isinstance(freq_val, list) and len(freq_val) > 0:
                freq_val = freq_val[0]
            clock.rate = int(freq_val)
        
        try:
            self.clock_tree.add_clock(clock)
        except ValueError:
            pass
    
    def _extract_devices(self) -> None:
        """Extract ordinary device nodes from the DTS tree."""
        def traverse(node: Dict[str, Any], depth: int = 0) -> None:
            if node.get("type") not in ("node", "root"):
                return

            node_name = node.get("name", "")
            props = node.get("properties", {})

            # skip root, "/" node, and clock/regulator/power nodes
            if node.get("type") == "node" and node_name != "/" and not any(
                x in node_name.lower() for x in ["clock", "regulator", "power"]
            ):
                # model as a generic IRNode device
                ir_node = IRNode(
                    name=node_name,
                    path=f"/{node_name}",
                    properties=dict(props),
                )
                self.devices[node_name] = ir_node

                # extract power-domain supply
                if "power-domains" in props:
                    self.device_supplies[node_name] = [str(props["power-domains"])]

                # extract clock references
                if "clocks" in props:
                    clocks = props["clocks"]
                    if isinstance(clocks, list):
                        self.device_clocks[node_name] = [str(c) for c in clocks]
                    else:
                        self.device_clocks[node_name] = [str(clocks)]
            
            # recurse
            for child in node.get("children", []):
                traverse(child, depth + 1)
        
        traverse(self.dts_tree)


def dts_to_soc(dts_tree: Dict[str, Any], soc_name: str = "unknown") -> SoC:
    """Map a DTS nested-dict tree to a SoC model."""
    mapper = DTSMapper(dts_tree, soc_name)
    return mapper.map()

"""Core graph model: components, typed dependency edges, registered interfaces."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

DEP_TYPES = ("hard", "runtime", "test", "generated")
# Per the specification only a ``hard`` dependency may be weakened, and only to
# one of the explicitly allowed weaker dependency types.
DEFAULT_WEAKEN_ORDER = ("runtime", "test", "generated")
WEAKER_THAN_HARD = {"runtime", "test", "generated"}

VALID_OPINIONS = ("accept", "reject", "alternative")


@dataclass(frozen=True)
class Component:
    id: str
    name: str = ""
    owner: str = ""
    weight: float = 1.0

    def label(self) -> str:
        return self.name or self.id

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "owner": self.owner, "weight": self.weight}

    @staticmethod
    def from_dict(d: dict) -> "Component":
        return Component(
            id=str(d["id"]),
            name=str(d.get("name", "")),
            owner=str(d.get("owner", "")),
            weight=float(d.get("weight", 1.0)),
        )


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    type: str = "hard"
    locked: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "locked": self.locked,
        }

    @staticmethod
    def from_dict(d: dict) -> "Edge":
        return Edge(
            id=str(d["id"]),
            source=str(d["source"]),
            target=str(d["target"]),
            type=str(d.get("type", "hard")),
            locked=bool(d.get("locked", False)),
        )


@dataclass(frozen=True)
class InterfaceSpec:
    """A *registered* alternative interface.

    ``replaces`` lists the concrete dependency edges that consumers stop using
    once they depend on the interface instead. The system never invents
    interfaces: candidates may only reference pre-registered specs.
    """

    id: str
    provider: str
    consumers: Tuple[str, ...]
    replaces: Tuple[str, ...]
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "consumers": list(self.consumers),
            "replaces": list(self.replaces),
            "description": self.description,
        }

    @staticmethod
    def from_dict(d: dict) -> "InterfaceSpec":
        consumers = tuple(str(c) for c in d.get("consumers", []))
        replaces = tuple(str(r) for r in d.get("replaces", []))
        return InterfaceSpec(
            id=str(d["id"]),
            provider=str(d["provider"]),
            consumers=consumers,
            replaces=replaces,
            description=str(d.get("description", "")),
        )


@dataclass
class Graph:
    components: Dict[str, Component] = field(default_factory=dict)
    edges: Dict[str, Edge] = field(default_factory=dict)
    interfaces: Dict[str, InterfaceSpec] = field(default_factory=dict)

    # -- construction -------------------------------------------------------
    def add_component(self, component: Component) -> None:
        self.components[component.id] = component

    def add_edge(self, edge: Edge) -> None:
        self.edges[edge.id] = edge

    def add_interface(self, spec: InterfaceSpec) -> None:
        self.interfaces[spec.id] = spec

    # -- queries ------------------------------------------------------------
    def owner_of(self, node: str) -> str:
        comp = self.components.get(node)
        return comp.owner if comp is not None else ""

    def weight_of(self, node: str) -> float:
        comp = self.components.get(node)
        return comp.weight if comp is not None else 1.0

    def edges_for_view(self, include_types: List[str]) -> List[Edge]:
        wanted = set(include_types)
        return [e for e in self.edges.values() if e.type in wanted]

    def interface_edges(self, spec: InterfaceSpec) -> List[Edge]:
        """Synthetic edges produced by introducing a registered interface.

        Every consumer depends (hard) on the interface node. The provider
        component *implements* the interface; under dependency inversion that
        implementation relationship is not a directed dependency edge, so the
        interface node has no outgoing edges and introducing it can never
        create a new cycle (it is always a sink).
        """
        out: List[Edge] = []
        for consumer in spec.consumers:
            out.append(
                Edge(
                    id="%s:%s->%s" % (spec.id, consumer, spec.id),
                    source=consumer,
                    target=spec.id,
                    type="hard",
                )
            )
        return out

    def validate(self) -> List[str]:
        errors: List[str] = []
        component_ids = set(self.components)
        for edge in self.edges.values():
            if edge.type not in DEP_TYPES:
                errors.append("edge %s has invalid type %r" % (edge.id, edge.type))
            if edge.source not in component_ids:
                errors.append("edge %s source %r is not a component" % (edge.id, edge.source))
            if edge.target not in component_ids:
                errors.append("edge %s target %r is not a component" % (edge.id, edge.target))
        for spec in self.interfaces.values():
            if spec.provider not in component_ids:
                errors.append("interface %s provider %r is not a component" % (spec.id, spec.provider))
            for consumer in spec.consumers:
                if consumer not in component_ids:
                    errors.append(
                        "interface %s consumer %r is not a component" % (spec.id, consumer)
                    )
            for replaced in spec.replaces:
                if replaced not in self.edges:
                    errors.append(
                        "interface %s replaces unknown edge %r" % (spec.id, replaced)
                    )
        return errors

    # -- (de)serialization --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "components": [self.components[k].to_dict() for k in sorted(self.components)],
            "edges": [self.edges[k].to_dict() for k in sorted(self.edges)],
            "interfaces": [self.interfaces[k].to_dict() for k in sorted(self.interfaces)],
        }

    @staticmethod
    def from_dict(d: dict) -> "Graph":
        graph = Graph()
        for cd in d.get("components", []):
            graph.add_component(Component.from_dict(cd))
        for ed in d.get("edges", []):
            graph.add_edge(Edge.from_dict(ed))
        for idict in d.get("interfaces", []):
            graph.add_interface(InterfaceSpec.from_dict(idict))
        return graph


@dataclass(frozen=True)
class AnalysisView:
    """A selected projection of the graph. The original graph is never mutated
    by creating a view -- filtering only decides which edge types participate
    in analysis."""

    include_types: Tuple[str, ...]

    @staticmethod
    def default() -> "AnalysisView":
        return AnalysisView(include_types=tuple(DEP_TYPES))

    def to_dict(self) -> dict:
        return {"include_types": list(self.include_types)}

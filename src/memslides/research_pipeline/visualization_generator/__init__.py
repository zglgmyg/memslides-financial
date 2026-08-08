"""Generate schema-valid Visualization JSON from outline intent and document evidence."""

from .contracts import (
    CandidateRejectionCode,
    CandidateTriggerCode,
    ExtractionProposal,
    NumericFact,
    ProposedSeries,
    VisualCandidate,
)
from .candidate_detection import (
    locate_corpus_candidates,
    locate_visual_candidates,
)
from .extraction import (
    LLMExtractionAdapter,
    map_extraction_proposal,
    proposal_from_block,
    proposal_from_table,
)
from .numeric_facts import (
    NumericFactLedger,
    block_numeric_facts,
    build_numeric_fact_ledger,
    table_numeric_facts,
)
from .verification import (
    VisualizationVerificationError,
    assemble_verified_chart,
    assemble_verified_table,
)
from .generate_visualizations import (
    GenerationIssue,
    VisualizationArtifact,
    VisualizationCoverageWarning,
    bindings_from_artifacts,
    generate_visualizations,
    preflight_visualizations,
    warn_for_render_args,
)
from .manifest import (
    LoadedVisualizationManifest,
    VisualizationManifestError,
    load_visualization_manifest,
)
from .audit import (
    FactBinding,
    NumericAuditError,
    audit_visualization_artifacts,
    serialize_numeric_fact,
    serialize_numeric_fact_ledger,
)

__all__ = [
    "CandidateRejectionCode",
    "CandidateTriggerCode",
    "ExtractionProposal",
    "NumericFact",
    "ProposedSeries",
    "VisualCandidate",
    "locate_corpus_candidates",
    "locate_visual_candidates",
    "LLMExtractionAdapter",
    "map_extraction_proposal",
    "proposal_from_block",
    "proposal_from_table",
    "NumericFactLedger",
    "block_numeric_facts",
    "build_numeric_fact_ledger",
    "table_numeric_facts",
    "VisualizationVerificationError",
    "assemble_verified_chart",
    "assemble_verified_table",
    "GenerationIssue",
    "VisualizationArtifact",
    "VisualizationCoverageWarning",
    "bindings_from_artifacts",
    "generate_visualizations",
    "preflight_visualizations",
    "warn_for_render_args",
    "LoadedVisualizationManifest",
    "VisualizationManifestError",
    "load_visualization_manifest",
    "FactBinding",
    "NumericAuditError",
    "audit_visualization_artifacts",
    "serialize_numeric_fact",
    "serialize_numeric_fact_ledger",
]

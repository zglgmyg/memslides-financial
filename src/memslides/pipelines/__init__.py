__all__ = ["GenerationPipeline", "RevisionPipeline", "ExportPipeline"]


def __getattr__(name: str):
    if name == "GenerationPipeline":
        from memslides.pipelines.generation import GenerationPipeline

        return GenerationPipeline
    if name == "RevisionPipeline":
        from memslides.pipelines.revision import RevisionPipeline

        return RevisionPipeline
    if name == "ExportPipeline":
        from memslides.pipelines.export import ExportPipeline

        return ExportPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

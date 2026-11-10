"""Pre-filtro determinístico: título, idioma, evidencia y descripción condensada."""

from __future__ import annotations

from job_radar.config import Prefilter
from job_radar.models import PrefilterResult, RawJob
from job_radar.prefilter.condense import Condenser
from job_radar.prefilter.language import EvidenceExtractor, HighGermanDetector, detect_lang
from job_radar.prefilter.title import TitleFilter


class Prefilterer:
    def __init__(self, cfg: Prefilter):
        self.cfg = cfg
        self.title = TitleFilter(cfg.title)
        self.evidence = EvidenceExtractor(cfg.evidence, cfg.description.snippet_context_chars)
        self.condenser = Condenser(cfg.condense, cfg.description.condensed_max_chars)
        self.high_german = HighGermanDetector(cfg.description.de_high_level)

    def run(self, job: RawJob) -> PrefilterResult:
        reason = self.title.check(job.title)
        desc = job.description or ""
        lang = detect_lang(desc, self.cfg.description.detect_langs)
        if reason is None and lang in self.cfg.description.exclude_langs:
            reason = f"desc_lang:{lang}"
        if reason is None and lang == "de" and (hit := self.high_german.find(desc)):
            reason = f"desc_lang:de_high ({hit[:40]})"
        if reason is None and not desc.strip():
            reason = "no_description"
        return PrefilterResult(
            excluded=reason is not None,
            reason=reason,
            desc_lang=lang,
            desc_condensed=self.condenser.condense(desc),
            snippets=self.evidence.extract(desc),
        )

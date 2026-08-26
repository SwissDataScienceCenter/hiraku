from __future__ import annotations

from dataclasses import dataclass

from .config import Config, load_config
from .database import Database
from .git import GitAdapter
from .launcher import Launcher
from .reports import ReportService
from .services import (
    AnalysisService,
    EventService,
    ExperimentService,
    GoalService,
    LineageService,
    ProposalService,
    RunService,
)


@dataclass
class HirakuApp:
    config: Config
    db: Database
    git: GitAdapter
    goals: GoalService
    experiments: ExperimentService
    runs: RunService
    lineage: LineageService
    proposals: ProposalService
    events: EventService
    analysis: AnalysisService
    launcher: Launcher
    reports: ReportService


def build_app(config: Config | None = None) -> HirakuApp:
    config = config or load_config()
    db = Database(config.state_path)
    db.migrate()
    git = GitAdapter(config.root)
    goals = GoalService(db, git)
    experiments = ExperimentService(db, git, goals)
    runs = RunService(db)
    lineage = LineageService(experiments)
    proposals = ProposalService(db, goals)
    events = EventService(db)
    analysis = AnalysisService(goals, experiments, runs, events)
    launcher = Launcher(config, db, git, goals, experiments, runs)
    reports = ReportService(config, goals, lineage, runs, proposals, events)
    return HirakuApp(
        config,
        db,
        git,
        goals,
        experiments,
        runs,
        lineage,
        proposals,
        events,
        analysis,
        launcher,
        reports,
    )

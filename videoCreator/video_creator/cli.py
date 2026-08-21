"""Command-line interface for deterministic video-creator projects."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .artifacts import read_json
from .images import AnimeIPAdapterImageProvider, SanaImageProvider
from .local_image_environment import (
    ACTIVE_FLAG, MODEL_ID, MODEL_REVISION, cache_root, run_local_images,
    ANIME_MODEL_ID, ANIME_MODEL_REVISION, IP_ADAPTER_MODEL_ID, IP_ADAPTER_MODEL_REVISION,
    REVIEW_MODEL_ID, REVIEW_MODEL_REVISION, setup_local_images,
)
from .local_audio_environment import (
    ACTIVE_FLAG as AUDIO_ACTIVE_FLAG, MODEL_ID as AUDIO_MODEL_ID,
    MODEL_REVISION as AUDIO_MODEL_REVISION, run_local_audio, setup_local_audio,
)
from .orchestrator import run_project
from .project import (
    RIGHTS_STATES, adapt_project_narration, align_project_subtitles, analyze_project_source, ingest_project_source,
    approve_project_analysis, compile_project_prompts, compile_project_timeline, enrich_project_scenes,
    evaluate_project,
    generate_project_character_references, generate_project_images, initialize_project,
    generate_project_narration_audio,
    review_project_character_references,
    review_project_images,
    generate_project_shot_pilot, review_project_shot_pilot,
    plan_project_storyboard, validate_project,
    render_project_video,
    plan_project_narration, segment_project_scenes, write_analysis_review_template,
    write_narration_response_template,
)


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    root = argparse.ArgumentParser(prog="video-creator")
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run or resume the complete autonomous pipeline")
    run.add_argument("workspace", type=Path)
    run.add_argument("manuscript", type=Path)
    run.add_argument("--project-id", default=None)
    run.add_argument("--title", default=None)
    run.add_argument("--rights-status", choices=sorted(RIGHTS_STATES), default="unverified")
    run.add_argument(
        "--series-library", type=Path, default=None,
        help="Shared character library reused across manuscript-part workspaces",
    )
    run.add_argument("--provider", choices=("fixture", "anime-ip-adapter"), default="anime-ip-adapter")
    run.add_argument("--candidates-per-item", type=int, default=1)
    run.add_argument("--maximum-attempts", type=int, default=2)
    run.add_argument("--inference-steps", type=int, default=20)
    run.add_argument("--guidance-scale", type=float, default=7.0)
    run.add_argument("--offline", action="store_true")
    initialize = commands.add_parser("init", help="Create a project workspace")
    initialize.add_argument("workspace", type=Path)
    initialize.add_argument("--project-id", required=True)
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--rights-status", choices=sorted(RIGHTS_STATES), default="unverified")
    ingest = commands.add_parser("ingest", help="Ingest a Markdown manuscript")
    ingest.add_argument("workspace", type=Path)
    ingest.add_argument("manuscript", type=Path)
    analyze = commands.add_parser("analyze", help="Create draft story/entity candidates")
    analyze.add_argument("workspace", type=Path)
    analyze.add_argument("manuscript", type=Path)
    template = commands.add_parser("analysis-review-template", help="Create entity review decisions")
    template.add_argument("workspace", type=Path)
    template.add_argument("output", type=Path)
    approve = commands.add_parser("approve-analysis", help="Apply complete entity decisions")
    approve.add_argument("workspace", type=Path)
    approve.add_argument("decisions", type=Path)
    narration = commands.add_parser("plan-narration", help="Create bounded adaptation units")
    narration.add_argument("workspace", type=Path)
    narration.add_argument("manuscript", type=Path)
    narration.add_argument("--maximum-source-characters", type=int, default=2400)
    adapt = commands.add_parser("adapt-narration", help="Apply complete adaptation responses")
    adapt.add_argument("workspace", type=Path)
    adapt.add_argument("manuscript", type=Path)
    adapt.add_argument("responses", type=Path)
    response_template = commands.add_parser(
        "narration-response-template", help="Create complete adaptation responses",
    )
    response_template.add_argument("workspace", type=Path)
    response_template.add_argument("output", type=Path)
    scenes = commands.add_parser("segment-scenes", help="Create draft scenes")
    scenes.add_argument("workspace", type=Path)
    scenes.add_argument("--maximum-blocks", type=int, default=2)
    enrich = commands.add_parser(
        "enrich-scenes", help="Automatically enrich and QA draft scenes",
    )
    enrich.add_argument("workspace", type=Path)
    enrich.add_argument("--acceptance-threshold", type=float, default=0.8)
    enrich.add_argument("--maximum-attempts", type=int, default=2)
    storyboard = commands.add_parser(
        "plan-storyboard", help="Automatically plan source-bound shots",
    )
    storyboard.add_argument("workspace", type=Path)
    storyboard.add_argument("--target-shot-seconds", type=float, default=15.0)
    prompts = commands.add_parser(
        "compile-prompts", help="Compile image prompts and reference defaults",
    )
    prompts.add_argument("workspace", type=Path)
    prompts.add_argument(
        "--style", default=None,
        help="Override the project's persistent visual style",
    )
    images = commands.add_parser(
        "generate-images", help="Generate and automatically rank image candidates",
    )
    images.add_argument("workspace", type=Path)
    images.add_argument("--candidates-per-item", type=int, default=2)
    images.add_argument("--maximum-attempts", type=int, default=2)
    images.add_argument("--provider", choices=("fixture", "sana", "anime-ip-adapter"), default="fixture")
    images.add_argument("--model-id", default=MODEL_ID)
    images.add_argument("--model-revision", default=MODEL_REVISION)
    images.add_argument("--inference-steps", type=int, default=20)
    images.add_argument("--guidance-scale", type=float, default=4.5)
    images.add_argument("--offline", action="store_true")
    references = commands.add_parser(
        "generate-character-references",
        help="Generate and automatically rank canonical character references",
    )
    references.add_argument("workspace", type=Path)
    references.add_argument("--candidates-per-item", type=int, default=2)
    references.add_argument("--maximum-attempts", type=int, default=2)
    references.add_argument("--provider", choices=("fixture", "sana"), default="fixture")
    references.add_argument("--model-id", default=MODEL_ID)
    references.add_argument("--model-revision", default=MODEL_REVISION)
    references.add_argument("--inference-steps", type=int, default=20)
    references.add_argument("--guidance-scale", type=float, default=4.5)
    references.add_argument("--offline", action="store_true")
    review_references = commands.add_parser(
        "review-character-references",
        help="Review references against source evidence with cached SmolVLM2",
    )
    review_references.add_argument("workspace", type=Path)
    review_references.add_argument("--offline", action="store_true")
    pilot = commands.add_parser("generate-shot-pilot", help="Generate a bounded real-shot pilot")
    pilot.add_argument("workspace", type=Path)
    pilot.add_argument("--shot-limit", type=int, default=4)
    pilot.add_argument("--candidates-per-item", type=int, default=1)
    pilot.add_argument("--maximum-attempts", type=int, default=2)
    pilot.add_argument("--provider", choices=("fixture", "sana", "anime-ip-adapter"), default="fixture")
    pilot.add_argument("--model-id", default=MODEL_ID)
    pilot.add_argument("--model-revision", default=MODEL_REVISION)
    pilot.add_argument("--inference-steps", type=int, default=20)
    pilot.add_argument("--guidance-scale", type=float, default=4.5)
    pilot.add_argument("--offline", action="store_true")
    pilot_review = commands.add_parser("review-shot-pilot", help="Semantically review the shot pilot")
    pilot_review.add_argument("workspace", type=Path)
    pilot_review.add_argument("--offline", action="store_true")
    image_review = commands.add_parser("review-images", help="Review all production images")
    image_review.add_argument("workspace", type=Path)
    image_review.add_argument("--offline", action="store_true")
    audio = commands.add_parser("generate-audio", help="Generate offline narration audio")
    audio.add_argument("workspace", type=Path)
    audio.add_argument("--offline", action="store_true")
    subtitles = commands.add_parser("align-subtitles", help="Align narration and write subtitles")
    subtitles.add_argument("workspace", type=Path)
    timeline = commands.add_parser("compile-timeline", help="Compile motion timeline and audio mix")
    timeline.add_argument("workspace", type=Path)
    render = commands.add_parser("render", help="Render and mux the final video")
    render.add_argument("workspace", type=Path)
    evaluate = commands.add_parser("evaluate", help="Run final encoded-media QA")
    evaluate.add_argument("workspace", type=Path)
    setup_images = commands.add_parser(
        "setup-local-images", help="Create imageEnv and prefetch local visual weights",
    )
    setup_images.add_argument("--offline", action="store_true")
    setup_audio = commands.add_parser("setup-local-audio", help="Create audioEnv and prefetch Kokoro")
    setup_audio.add_argument("--offline", action="store_true")
    validate = commands.add_parser("validate", help="Validate project artifacts")
    validate.add_argument("workspace", type=Path)
    status = commands.add_parser("status", help="Show project stage states")
    status.add_argument("workspace", type=Path)
    return root


def main(argv: list[str] | None = None) -> None:
    """Run one video-creator command."""
    args = parser().parse_args(argv)
    if args.command == "run":
        raw_arguments = list(argv) if argv is not None else sys.argv[1:]
        if os.environ.get(ACTIVE_FLAG) != "1":
            raise SystemExit(run_local_images(raw_arguments))
        provider = None if args.provider == "fixture" else AnimeIPAdapterImageProvider(
            ANIME_MODEL_ID, model_revision=ANIME_MODEL_REVISION,
            adapter_model_id=IP_ADAPTER_MODEL_ID,
            adapter_revision=IP_ADAPTER_MODEL_REVISION,
            inference_steps=args.inference_steps, guidance_scale=args.guidance_scale,
        )
        result = run_project(
            args.workspace, args.manuscript,
            project_id=args.project_id or args.workspace.name,
            title=args.title or args.manuscript.stem,
            rights_status=args.rights_status, series_library=args.series_library,
            image_provider=provider, candidates_per_item=args.candidates_per_item,
            maximum_attempts=args.maximum_attempts, offline=args.offline,
        )
    elif args.command == "setup-local-images":
        python = setup_local_images(offline=args.offline)
        result = {
            "status": "ready", "environment_python": str(python),
            "model": MODEL_ID, "revision": MODEL_REVISION,
            "license": "Apache-2.0", "cache_root": str(cache_root()),
            "review_model": REVIEW_MODEL_ID,
            "review_revision": REVIEW_MODEL_REVISION,
            "review_license": "Apache-2.0",
            "anime_model": ANIME_MODEL_ID, "anime_revision": ANIME_MODEL_REVISION,
            "anime_license": "CreativeML Open RAIL++-M",
            "identity_adapter": IP_ADAPTER_MODEL_ID,
            "identity_adapter_revision": IP_ADAPTER_MODEL_REVISION,
            "identity_adapter_license": "Apache-2.0",
        }
    elif args.command == "setup-local-audio":
        python = setup_local_audio(offline=args.offline)
        result = {"status": "ready", "environment_python": str(python), "model": AUDIO_MODEL_ID,
                  "revision": AUDIO_MODEL_REVISION, "license": "Apache-2.0"}
    elif args.command == "init":
        result = initialize_project(args.workspace, args.project_id, args.title, args.rights_status)
    elif args.command == "ingest":
        result = ingest_project_source(args.workspace, args.manuscript)
    elif args.command == "analyze":
        result = analyze_project_source(args.workspace, args.manuscript)
    elif args.command == "analysis-review-template":
        result = write_analysis_review_template(args.workspace, args.output)
    elif args.command == "approve-analysis":
        result = approve_project_analysis(args.workspace, args.decisions)
    elif args.command == "plan-narration":
        result = plan_project_narration(
            args.workspace, args.manuscript,
            maximum_source_characters=args.maximum_source_characters,
        )
    elif args.command == "adapt-narration":
        result = adapt_project_narration(args.workspace, args.manuscript, args.responses)
    elif args.command == "narration-response-template":
        result = write_narration_response_template(args.workspace, args.output)
    elif args.command == "segment-scenes":
        result = segment_project_scenes(args.workspace, maximum_blocks=args.maximum_blocks)
    elif args.command == "enrich-scenes":
        result = enrich_project_scenes(
            args.workspace, acceptance_threshold=args.acceptance_threshold,
            maximum_attempts=args.maximum_attempts,
        )
    elif args.command == "plan-storyboard":
        result = plan_project_storyboard(
            args.workspace, target_shot_seconds=args.target_shot_seconds,
        )
    elif args.command == "compile-prompts":
        result = compile_project_prompts(args.workspace, style=args.style)
    elif args.command in {"generate-images", "generate-character-references", "generate-shot-pilot"}:
        if args.provider in {"sana", "anime-ip-adapter"} and os.environ.get(ACTIVE_FLAG) != "1":
            raw_arguments = list(argv) if argv is not None else sys.argv[1:]
            raise SystemExit(run_local_images(raw_arguments))
        if args.provider == "fixture":
            provider = None
        elif args.provider == "anime-ip-adapter":
            provider = AnimeIPAdapterImageProvider(
                ANIME_MODEL_ID, model_revision=ANIME_MODEL_REVISION,
                adapter_model_id=IP_ADAPTER_MODEL_ID,
                adapter_revision=IP_ADAPTER_MODEL_REVISION,
                inference_steps=args.inference_steps, guidance_scale=args.guidance_scale,
            )
        else:
            provider = SanaImageProvider(
                args.model_id, model_revision=args.model_revision,
                inference_steps=args.inference_steps, guidance_scale=args.guidance_scale,
            )
        if args.command == "generate-shot-pilot":
            result = generate_project_shot_pilot(
                args.workspace, provider, shot_limit=args.shot_limit,
                candidates_per_item=args.candidates_per_item,
                maximum_attempts=args.maximum_attempts,
            )
        else:
            generator = (
                generate_project_images if args.command == "generate-images"
                else generate_project_character_references
            )
            result = generator(
                args.workspace, provider, candidates_per_item=args.candidates_per_item,
                maximum_attempts=args.maximum_attempts,
            )
    elif args.command == "review-character-references":
        if os.environ.get(ACTIVE_FLAG) != "1":
            raw_arguments = list(argv) if argv is not None else sys.argv[1:]
            raise SystemExit(run_local_images(raw_arguments))
        result = review_project_character_references(args.workspace)
    elif args.command == "generate-audio":
        if os.environ.get(AUDIO_ACTIVE_FLAG) != "1":
            raw_arguments = list(argv) if argv is not None else sys.argv[1:]
            raise SystemExit(run_local_audio(raw_arguments))
        result = generate_project_narration_audio(args.workspace)
    elif args.command == "align-subtitles":
        result = align_project_subtitles(args.workspace)
    elif args.command == "compile-timeline":
        result = compile_project_timeline(args.workspace)
    elif args.command == "render":
        result = render_project_video(args.workspace)
    elif args.command == "evaluate":
        result = evaluate_project(args.workspace)
    elif args.command in {"review-shot-pilot", "review-images"}:
        if os.environ.get(ACTIVE_FLAG) != "1":
            raw_arguments = list(argv) if argv is not None else sys.argv[1:]
            raise SystemExit(run_local_images(raw_arguments))
        result = (
            review_project_shot_pilot(args.workspace)
            if args.command == "review-shot-pilot" else review_project_images(args.workspace)
        )
    elif args.command == "validate":
        issues = validate_project(args.workspace)
        result = {"passed": not issues, "issues": issues}
    else:
        manifest = read_json(args.workspace / "project.json")
        result = {
            "project_id": manifest["project_id"], "rights": manifest["rights"],
            "stages": manifest["stages"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "validate" and not result["passed"]:
        raise SystemExit(1)
    if args.command == "run" and result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

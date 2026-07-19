from pathlib import Path

from markdownCleaner.pipeline import OCRPipeline


def main():
    root = Path(__file__).resolve().parent
    input_file = root / "Overlord v01.md"
    config_file = root / "config.yaml"

    pipeline = OCRPipeline(config_file)
    result = pipeline.run(input_file)

    print("\nPipeline completed.")
    print(f"Total corrections: {pipeline.context.total_changes}")
    print(f"Output: {result['output']['markdown']}")


if __name__ == "__main__":
    main()

from pathlib import Path

from markdownCleaner.pipeline import OCRPipeline


def main():

    input_file = Path("Overlord v01.md")

    pipeline = OCRPipeline()

    pipeline.process(input_file)


if __name__ == "__main__":
    main()

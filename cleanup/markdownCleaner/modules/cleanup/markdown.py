import re


class MarkdownProcessor:

    name = "Markdown"

    def __init__(self, context):

        self.context = context

    def process(self, segment):

        text = segment.current_text

        original = text

        # HTML line breaks

        text = text.replace("<br>", "\n")

        # underline tags

        text = re.sub(r"</?u>", "", text)

        # remove empty HTML comments

        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)

        # normalize spaces

        text = re.sub(r"[ \t]+", " ", text)

        if text != original:

            segment.update(text)

            self.context.increment("markdown_cleaned")

            return True

        return False

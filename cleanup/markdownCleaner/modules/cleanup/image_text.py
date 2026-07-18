import re


class ImageTextProcessor:


    name = "ImageText"



    def __init__(self, context):

        self.context = context



    def process(self, segment):

        pattern = (
            r"<!-- Start of picture text -->.*?"
            r"<!-- End of picture text -->"
        )


        cleaned = re.sub(
            pattern,
            "",
            segment.current_text,
            flags=re.DOTALL
        )


        if cleaned != segment.current_text:

            segment.update(cleaned)

            self.context.increment(
                "image_text_removed"
            )

            return True


        return False
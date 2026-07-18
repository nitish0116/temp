import re


class FrontMatterProcessor:


    name="FrontMatter"



    def __init__(self, context):

        self.context=context



    def process(self, segment):

        text = segment.current_text


        rules = [

            # copyright section
            (
                r"#?\s*Copyright.*?"
                r"(?=##\s*\*{0,2}Contents)"
            ),


            # Yen Press metadata
            (
                r"Yen On.*?"
                r"(?=###)"
            ),


            # ISBN lines
            (
                r"ISBN[:\s].*?\n"
            ),

        ]


        original=text


        for rule in rules:

            text=re.sub(
                rule,
                "",
                text,
                flags=re.I|re.S
            )


        if text != original:

            segment.update(text)

            self.context.increment(
                "frontmatter_removed"
            )

            return True


        return False
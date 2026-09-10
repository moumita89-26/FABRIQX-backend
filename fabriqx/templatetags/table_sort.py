from django import template

register = template.Library()


@register.simple_tag
def column_sort_url(cl, index, direction):
    ordering = f"-{index}" if direction == "desc" else str(index)
    return cl.get_query_string({"o": ordering}, ["p"])

"""The built-in candidate catalogue: the 50 places every engine may answer with.

This is the closed label set at every level. The gazetteer matches against
these names, both classifiers are given them in the prompt and a reply naming
anything else is dropped, and the encoders embed a string built from each one.
A place onboarded at run time is appended to the live copy the server hands
every engine, so a request's catalogue can be longer than this list.

`_coords.py` holds each place's coordinate, scale and provenance. The first 22
are the seed catalogue; the remaining 28 were added for the WNUT-2016
evaluation.
"""

DEFAULT_CITIES: list[str] = [
    "Singapore",
    "Tengah Plantation Crescent",
    "Tampines",
    "Jurong East",
    "Punggol",
    "Bedok",
    "Woodlands",
    "Kuala Lumpur",
    "Petaling Jaya",
    "Jakarta",
    "Pekanbaru",
    "Bangkok",
    "Manila",
    "Ho Chi Minh City",
    "Hong Kong",
    "Tokyo",
    "Seoul",
    "Sydney",
    "London",
    "New York",
    "San Francisco",
    "Toronto",
    # Added for the WNUT-2016 evaluation: the most frequent metropolitan
    # areas in that benchmark not already covered above, so more of its
    # geotag-labelled tweets map into the closed catalogue. Centroids in
    # _coords.py are taken from the WNUT gold city centroids.
    "Los Angeles",
    "Bandung",
    "Istanbul",
    "Chicago",
    "Sao Paulo",
    "Rio de Janeiro",
    "Denpasar",
    "Surabaya",
    "Atlanta",
    "Medan",
    "Dallas",
    "Miami",
    "Izmir",
    "Makassar",
    "Las Vegas",
    "Lagos",
    "Yogyakarta",
    "Austin",
    "Malang",
    "San Diego",
    "Dublin",
    "San Antonio",
    "Houston",
    "Buenos Aires",
    "Cleveland",
    "Philadelphia",
    "Curitiba",
    "Charlotte",
]

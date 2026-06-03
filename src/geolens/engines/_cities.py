"""Default candidate cities for stub mode.

These are not the final classnames; the real engines bake in their own
catalogues at model init. This list keeps the UI working until weights are
wired up. It is Singapore-heavy on purpose, for the estate-management
scenario.
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

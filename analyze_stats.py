import pstats

# Create a pstats object
p = pstats.Stats("stats.prof")

# Sort the statistics by the total time spent in the function (and its sub-functions)
# and print the top 20 lines.
print("--- Top 20 by Cumulative Time ---")
p.sort_stats("cumulative").print_stats(20)

# Sort by the time spent in the function alone, ignoring sub-calls,
# and print the top 20 lines. This shows your true bottlenecks.
print("\n--- Top 20 by Total Time (Self) ---")
p.sort_stats("tottime").print_stats(20)
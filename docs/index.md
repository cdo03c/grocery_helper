# 🛒 Grocery Helper

A Python library, command-line interface, and Streamlit application that integrates with **AnyList** to find optimal grocery pricing across stores.

---

## What is Grocery Helper?

Most shoppers create a grocery list in AnyList and then wonder:
> *"Where should I buy these groceries to save the most money, and is it worth visiting multiple stores?"*

**Grocery Helper** answers this question by:
1. Importing your grocery lists from AnyList (or using built-in demo lists).
2. Fetching real-time and regional prices across major grocers (**ALDI**, **Trader Joe's**, **Kroger**, and **Whole Foods**).
3. Analyzing the trade-offs:
   - **Split Basket**: Cheapest overall price by buying each item at its cheapest store.
   - **Single Store**: The best single store if you only want to make one stop.
   - **Constrained Stops**: Limit your route to at most $N$ stores (e.g. at most 2 stops).
4. Exporting store-specific sub-lists back into AnyList with one click!

---

## Quick Example

=== "CLI"

    ```bash
    # Optimize a list in offline demo mode
    grocery-helper optimise list_001 --demo

    # Output:
    # ======================================================================
    # 🛒 Grocery Helper: Weekly Shop (6 items)
    # ZIP Code: 90210 | Mode: Demo Mode
    # ======================================================================
    # Item                     Qty    Best Store       Price      Savings
    # ----------------------------------------------------------------------
    # Milk                     1 gal  ALDI             $2.85      $1.69
    # Sourdough Bread          1 ea   ALDI             $2.20      $0.41
    # Free Range Eggs          1 ea   ALDI             $2.68      $1.19
    # Honeycrisp Apples        3 lb   ALDI             $1.73      $2.79
    # Chicken Breast           2 lb   Kroger           $4.09      $2.30
    # Spinach                  1 ea   ALDI             $2.04      $0.36
    # ----------------------------------------------------------------------
    # Optimised Split Basket: $23.14 (Potential Savings: $8.74)
    #
    # Single-Store Basket Totals:
    #   • ALDI          : $23.44 (+$0.30 vs split) ⭐ Best single-trip store
    #   • Kroger        : $26.37 (+$3.23 vs split)
    #   • Trader Joe's  : $26.68 (+$3.54 vs split)
    #   • Whole Foods   : $31.88 (+$8.74 vs split)
    # ======================================================================
    ```

=== "Streamlit Web UI"

    ```bash
    grocery-helper serve
    # Or:
    uv run streamlit run src/grocery_helper/app.py
    ```

---

## Next Steps

- Want a simple, non-technical walkthrough? Read our [Shopper's Guide](guide/user-guide.md).
- Ready to install and configure? Check out the [Getting Started](getting-started.md) guide.
- Read about the [System Architecture](architecture.md).
- Learn about the [Streamlit Web UI](guide/streamlit.md) and [CLI Reference](guide/cli.md).


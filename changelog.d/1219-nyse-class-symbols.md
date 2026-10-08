- alpaca_prices: NYSE class tickers written undotted (BFA, BFB, HVTA, WSOB) are asked as BF.A etc. via alpaca.class_symbols; needs a bar backfill of the four ids.
### Fixed
- Brown-Forman, Hevi and Watsco class shares (NYSE, ticker written without the dot) are asked of Alpaca in dotted form through the new alpaca.class_symbols map (#1219).

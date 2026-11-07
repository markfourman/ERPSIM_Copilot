# AI prompts to build your team's files

The co-pilot is only as good as the memory you give it. These prompts turn the notes, screenshots and spreadsheets you
already have into the files it needs. Paste them into any AI chat (Gemini, ChatGPT, Claude…), attach or paste your
material where it says, then save the answer into the file named at the top of each prompt.

**Before you start**
- Never paste passwords, API keys or anyone's personal details.
- Check the AI's numbers against your own. The prompts tell it not to invent facts, but read the result anyway.
- Keep the three memory files together under ~25,000 characters. Shorter answers come back faster.

---

## 1. `memory/private/strategy.md`: your playbook

*Attach or paste: your strategy notes, guides, chat transcripts with teammates, anything describing how you play.*

```
I play the SAP ERPsim Manufacturing (muesli) game. Below are my team's notes about how we play.
Turn them into a strategy file that an AI advisor will read before every question.

Rules:
- Use exactly these headings: Products and mix; Recipes (ZCS02); Opening prices (per channel 10 / 12 / 14);
  Price moves; Forecast and buying (MD61, MD01, ME59N); Production (CO41); Stock targets; Money
  (setup time, capacity, loan, marketing); Round 4 finish.
- Short bullet points. Keep every number we gave. Use material codes (e.g. F16) where we used them.
- Do not invent rules or numbers. If something is missing or unclear, write "TO DECIDE:" and the question.
- Plain language, no filler. Under 1,200 words.

Our notes:
[PASTE OR ATTACH HERE]
```

## 2. `memory/private/lessons.md`: what each game taught you

*Attach: the leaderboard screenshot(s), your debrief, or notes from the game. Run it once per game and add the result
to the end of the file.*

```
Here is information about one ERPsim Manufacturing game my team played (leaderboard, notes, debrief).
Write a lessons entry for an AI advisor that will read it before future games.

Format:
## Game: <date> (<practice / tournament>, <number of teams>)
- Result: rank, company valuation, profit, credit rating.
- Profit by round (if known).
- What worked (with numbers).
- What cost us (with numbers and, if possible, an estimate of what it cost).
- What we change next time (one line each, as rules we can follow).

Only use facts in the material. Mark guesses with "(estimate)". Under 300 words.

Material:
[PASTE OR ATTACH HERE]
```

## 3. `memory/private/game_rules.md`: facts you have checked

*Attach: your instructor's slides, the ERPsim job aid, the rules shown at the start of the game.*

```
From the attached ERPsim Manufacturing (muesli) material, list the game facts that matter for decisions:
factory capacity and lot sizes, setup times, lead times, storage limits and costs, how the company valuation and
credit rating are calculated, interest, marketing, channels (10 / 12 / 14), round length, anything about CO2 or
sustainability, and any special rules for our event.

Rules: one bullet per fact, numbers exactly as written, the source in brackets (e.g. "[slide 12]").
Do not add facts that are not in the material. If two sources disagree, show both. Under 600 words.

Material:
[ATTACH HERE]
```

## 4. `config.yaml`: products, mix, recipes and costs

*Attach: your ZCS02 recipe screens and the ZME13 supplier price list (screenshots are fine), plus which products you plan
to make and in what proportion.*

```
Write the "products:" section of a YAML config file for an ERPsim Manufacturing (muesli) advisor.

Use the attached recipe screens (ZCS02) and supplier prices (ZME13). For each product we make or might make:
- name: exactly one of these: 1kg Mixed Fruit Muesli, 1kg Nut Muesli, 1kg Blueberry Muesli, 1kg Strawberry Muesli,
  1kg Raisin Muesli, 1kg Original Muesli, 500g Nut Muesli, 500g Blueberry Muesli, 500g Strawberry Muesli,
  500g Raisin Muesli, 500g Original Muesli, 500g Mixed Fruit Muesli
- plan: core, reserve or never
- mix: our share of batches as a number (e.g. 3 out of 8 batches = 3)
- recipe: kg per box for each ingredient, plus 1 box and 1 bag. Ingredient names exactly: Oats, Wheat, Nuts,
  Blueberries, Strawberries, Raisins, "Large Box (1kg)", "Large Bag (1kg)", "Small Box (500g)", "Small Bag (500g)"
- cost_per_box: sum of (kg x supplier price) for every ingredient and packaging item, rounded to 2 decimals.
  Show the calculation in a comment.

Example of the format (default game recipe, not ours):
products:
  - name: 1kg Blueberry Muesli
    plan: core
    mix: 2
    cost_per_box: 2.50   # 0.35 wheat x 1.12 + 0.35 oats x 0.65 + 0.30 blueberries x 4.65 + box 0.33 + bag 0.16
    recipe: {Wheat: 0.35, Oats: 0.35, Blueberries: 0.30, "Large Box (1kg)": 1, "Large Bag (1kg)": 1}

Our plan: [WHICH PRODUCTS, CORE OR RESERVE, MIX]
Attached: recipe screens and supplier prices.
Only output valid YAML. If a recipe or price is missing, put "# TO CHECK" next to it instead of guessing.
```

## 5. Lessons from a saved game workbook

*Attach: a workbook from your `history/` folder (saved with the app's "Save this game to history" button). Needs an AI
chat that can read Excel files.*

```
The attached workbook is one ERPsim Manufacturing game: one sheet per OData table (Company_Valuation, Sales, Inventory,
Production, Production_Orders, Purchase_Orders, Market, ...). Our sales are in Sales; Market is all teams together.

Work out, with numbers:
1. Profit by round (from Company_Valuation PROFIT at days 20, 40, 60, 80).
2. Days the factory made nothing, and production orders smaller than 48,000.
3. Days each product was out of stock (Inventory opening balance = 0).
4. For our main products, our average price vs other teams' average price per week (Market minus our Sales).
5. Weeks where total finished goods kept rising while sales fell.
6. Finished goods and ingredients left on the last day.

Then write a lessons entry in this format: Result / What worked / What cost us / What we change next time.
Only use numbers you calculated from the file. Under 300 words.
```

## 6. Tidy up your memory

*Paste: all your memory files.*

```
Below are the memory files an AI advisor reads before every question about our ERPsim game.
Make them shorter and clearer without losing any rule or number:
- remove repeats, merge overlapping points;
- if two points contradict each other, keep both and flag them with "CONFLICT:";
- keep the same file names and headings; total under 20,000 characters.
Return each file separately, starting with its file name.

[PASTE strategy.md, lessons.md and game_rules.md]
```

---

## Good questions to ask the co-pilot during a game

- What should we produce next, and how much? What exactly do I type in MD61?
- Which prices should we change right now? Give me the new prices per channel.
- Are rivals undercutting us on anything?
- We have too much stock: what do we cut, and what do we stop making?
- Will anything be left over on day 80 at this pace?
- Capacity or pay down the loan, with €1M?
- Is our rating safe for day 80?
- What did we do differently in our best past game compared with now?

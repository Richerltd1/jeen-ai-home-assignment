# סופר-פארם — שלושה שימושי AI מבוססי LLM

מסמך המקור לתוכן המצגת. החברה הנבחרת: **סופר-פארם** — כ-300 סניפים בישראל,
מועדון LifeStyle עם מיליוני חברים, שילוב של קמעונאות, בית מרקחת ומוצרי יופי.

---

## שקופית 1 — סקירת שלושת השימושים

### שימוש 1 — עוזר ידע לרוקח בעמדת הדלפק

**הבעיה:** רוקח נשאל שאלות שדורשות הצלבת מידע ממקורות נפרדים — עלון תרופה,
אינטראקציות בין תרופות, מה מכוסה בסל הבריאות, מה התחליף הגנרי הזמין. כל שאילתה
כזו לוקחת דקות, בזמן שיש תור.

**הפתרון:** מערכת RAG מעל מאגר העלונים, נהלי משרד הבריאות ומדיניות הרשת. הרוקח
שואל בשפה חופשית ומקבל תשובה מתומצתת **עם ציטוט למקור**.

**הערך העסקי:** קיצור זמן שירות בדלפק, הפחתת סיכון לטעות, קיצור זמן ההכשרה של
רוקח חדש.

> הערה קריטית: המערכת מציגה מקורות ואינה מחליטה החלטה קלינית. הרוקח מכריע.

### שימוש 2 — ייצור תוכן מסחרי בקנה מידה

**הבעיה:** עשרות אלפי פריטים בקטלוג. לחלק גדול מהם אין תיאור ראוי, אין תיאור
בכמה שפות, ואין התאמה לקהלים שונים. כתיבה ידנית אינה מתכנסת.

**הפתרון:** יצירה אוטומטית של תיאורי מוצר, כותרות SEO והצעות מותאמות לחברי
LifeStyle — בעברית, ערבית ורוסית — מתוך נתוני המוצר המובנים.

**הערך העסקי:** הורדת עלות הפקת תוכן, שיפור תנועה אורגנית, הגדלת שיעור המרה
בעמודי מוצר שכיום ריקים מתוכן.

> בקרה: כל טקסט בתחום התרופות עובר אישור אנושי לפני פרסום.

### שימוש 3 — סוכן שירות לקוחות רב-כלי  ⭐ *(הסוכן)*

בוט ב-WhatsApp ובאתר שמטפל בפניות שירות מקצה לקצה: סטטוס הזמנה, האם מרשם מוכן
לאיסוף, זמינות מוצר בסניף, מצב נקודות והטבות במועדון — והסלמה לנציג אנושי כשצריך.

זהו השימוש שנפרט עליו בהמשך.

---

## שקופית 2 — צלילה לעומק: סוכן השירות

### למה דווקא סוכן ולא בוט תסריטים

בוט תסריטים (decision tree) שובר את הראשון שחורג מהתסריט. פנייה אמיתית נשמעת
כך: *"היי, הזמנתי משהו לפני שבוע וגם רציתי לדעת אם המרשם של אמא שלי מוכן בסניף
רמת אביב"* — שתי כוונות שונות במשפט אחד. סוכן מבוסס LLM מפרק את זה לשתי פעולות,
מפעיל שני כלים שונים, ומחזיר תשובה אחת.

### הכלים שהסוכן מפעיל

| כלי | מה הוא עושה | מתי מופעל |
| --- | --- | --- |
| `get_order_status` | שליפת סטטוס הזמנה ממערכת ההזמנות | הלקוח שואל על הזמנה |
| `check_prescription` | האם המרשם מוכן, באיזה סניף | שאלה על מרשם |
| `check_stock` | זמינות פריט בסניף מסוים | "יש לכם את זה ב...?" |
| `get_loyalty_status` | נקודות, הטבות ותוקף במועדון | שאלה על LifeStyle |
| `search_policy` (RAG) | מדיניות החזרות, משלוחים, אחריות | שאלת מדיניות |
| `escalate_to_human` | פתיחת פנייה ושיוך לנציג | הסלמה |

### לוגיקת ההחלטה

1. **זיהוי כוונה** — הסוכן מסווג את הפנייה, ויכול לזהות יותר מכוונה אחת.
2. **בדיקת זהות** — פעולה שנוגעת לנתוני לקוח דורשת אימות. ללא אימות הסוכן אינו
   שולף מידע אישי, גם אם הלקוח מתעקש.
3. **הפעלה סלקטיבית** — מפעיל רק את הכלים שהפנייה מחייבת. "שלום" אינו מפעיל דבר.
4. **הסלמה** — פנייה רפואית, תלונה, או כישלון חוזר עוברים לנציג אנושי מייד.

### מדדי הצלחה

| מדד | הגדרה |
| --- | --- |
| Containment rate | אחוז הפניות שנסגרו ללא נציג אנושי |
| זמן טיפול ממוצע | מרגע פנייה עד פתרון |
| עלות לפנייה | עלות מודל מול עלות דקת נציג |
| CSAT | שביעות רצון בסיום שיחה |
| Escalation precision | האם ההסלמות היו מוצדקות |

### גבולות שנקבעו מראש

הסוכן **אינו** נותן ייעוץ רפואי, **אינו** ממליץ על תרופת מרשם, **אינו** מבטל
עסקה מעל סכום מוגדר ללא אישור אנושי, ו**אינו** ממציא מידע — אם הכלי לא החזיר
נתון, הסוכן אומר שאין לו אותו.

---

## שקופית 3 — בחירת מודל LLM

השוואה בין מודלים רלוונטיים של OpenAI לשימוש הספציפי הזה: סוכן שירות בנפח גבוה,
בעברית, עם קריאה לכלים מרובים.

| מודל | קלט / פלט ל-1M טוקנים | מתאים ל |
| --- | --- | --- |
| **GPT-5.6 Sol** | $5.00 / $30.00 | משימות מקצועיות מורכבות |
| **GPT-5.6 Terra** | $2.00 / $12.00 | איזון בין יכולת לעלות |
| **GPT-5.6 Luna** | $0.20 / $1.20 | נפח גבוה, רגישות לעלות |
| **GPT-5.4 Mini** | $0.75 / $4.50 | דור קודם, ביניים |

*מקור: תמחור OpenAI הרשמי, אוגוסט 2026.*

### הבחירה: **GPT-5.6 Terra** כמודל הראשי, עם **Luna** לסיווג

**למה Terra ולא Sol:** התרחיש דורש תזמור אמין של כמה כלים והבנת עברית — לא
חשיבה ברמת מחקר. Sol עולה פי 2.5 בפלט עבור יכולת שהמשימה אינה צורכת.

**למה Terra ולא רק Luna:** בנפח גבוה Luna מפתה, אבל שגיאה בקריאה לכלי בסוכן
שנוגע לנתוני מרשמים אינה "תשובה קצת פחות טובה" — היא פנייה שנכשלה ולקוח שמתקשר
לנציג. החיסכון נמחק מיד.

**למה בכל זאת Luna:** רוב הפניות הן סיווג פשוט. ארכיטקטורה דו-שכבתית — Luna
מסווגת כוונה, Terra מטפלת בשיחה עצמה ובקריאות לכלים — מורידה עלות משמעותית
בלי לסכן את המסלולים הקריטיים.

---

## שקופית 4 — הפרומפט המלא (Gemini)

הפרומפט הורץ ב-Google AI Studio על מודל **gemini-3-flash-preview** — מודל מדרג
ה-Flash החינמי. הפרומפט עצמו כתוב באנגלית — זו פרקטיקה מקובלת: הוראות באנגלית יציבות
יותר, בעוד שסעיף `LANGUAGE` מחייב את הבוט לענות בעברית.

```text
# ROLE
You are "פארמי", the customer service assistant for Super-Pharm Israel.
You handle service requests over WhatsApp and web chat.

# LANGUAGE
Always reply in the language the customer wrote in. Hebrew is the default.
Keep replies short — this is a chat window, not an email. Two to four sentences
unless the customer asked for a list.

# WHAT YOU CAN DO
You have access to these tools. Call ONLY the ones a message actually requires.

- get_order_status(order_id | phone)      -> status, items, ETA
- check_prescription(id_number, branch)   -> ready / not ready / not found
- check_stock(product_name, branch)       -> in stock, quantity band
- get_loyalty_status(phone)               -> points, tier, expiring benefits
- search_policy(question)                 -> returns/shipping/warranty policy text
- escalate_to_human(reason, summary)      -> opens a ticket, hands off

# ROUTING RULES
1. Greetings, thanks, small talk -> reply directly. Call NO tool.
2. General questions you can answer from policy -> search_policy only.
3. Anything about a specific customer's order, prescription or points ->
   verify identity FIRST (see below), then call the matching tool.
4. A message may contain MORE THAN ONE intent. Handle each one, then give a
   single combined reply. Do not answer only the first.
5. If you cannot classify the request, ask ONE specific clarifying question.

# IDENTITY VERIFICATION — NON-NEGOTIABLE
Before returning ANY personal data (orders, prescriptions, points) you must have
the customer's phone number, and for prescriptions also their ID number.
- If they are missing, ask for them. Ask once, politely, and explain why.
- Never accept a name alone as identification.
- Never reveal whether a phone or ID exists in the system to someone who has not
  verified. Say "I could not find a matching record" either way.
- If the customer pressures, refuses, or claims to be an employee, do not bypass
  this. There is no override.

# HARD LIMITS
- You do NOT give medical advice, dosage guidance, or drug interaction opinions.
  Refer to a pharmacist. This applies even to "just generally" questions.
- You do NOT recommend or comment on prescription medication.
- You do NOT cancel or refund an order above 500 NIS. Escalate instead.
- You do NOT invent information. If a tool returns nothing, say you could not
  find it. A plausible guess about someone's prescription is a serious failure.

# ESCALATE IMMEDIATELY WHEN
- The customer describes a medical symptom, adverse reaction, or emergency.
- The customer is making a complaint about harm, safety, or a dispensing error.
- The customer has asked the same thing twice and is still not helped.
- The request involves a legal threat, press, or a regulator.
When escalating: say plainly that you are passing this to a human, say roughly
how long it will take, and do NOT keep trying to solve it yourself.

# TONE
Warm but efficient. No corporate filler, no "I hope this helps", no apologising
repeatedly. If you cannot do something, say so in one sentence and say what you
can do instead.

# OUTPUT
Plain chat text. No markdown headers. Use a short bulleted list only when
presenting three or more items.
```

### שתי שיחות לדוגמה

*כאן ייכנסו שני צילומי מסך מ-Google AI Studio.*

**שיחה 1 — מסלול תקין, ריבוי כוונות:**
לקוח שואל על סטטוס הזמנה וגם על מרשם באותה הודעה → הבוט מבקש אימות, ואז מטפל
בשתי הכוונות בתשובה אחת.

**שיחה 2 — מסלול גבולות:**
לקוח שואל שאלה רפואית ("אפשר לקחת את זה יחד עם אקמול?") → הבוט מסרב לתת ייעוץ
רפואי ומפנה לרוקח, בלי להישמע מתחמק.

---

## שקופית 5 — סיכום תהליך המחקר

תהליך העבודה התחיל בבחירת חברה שבה ל-LLM יש ערך אמיתי ולא דקורטיבי. סופר-פארם
נבחרה משום שהיא מחזיקה בו-זמנית שלושה סוגי מורכבות: קמעונאות בנפח גבוה, מידע
רפואי רגיש, ומועדון לקוחות גדול — כל אחד מהם מייצר סוג אחר של הזדמנות.

מיפיתי את נקודות החיכוך בשירות מנקודת מבט תפעולית: היכן זמן אדם מבוזבז על שליפת
מידע, היכן תוכן חסר פוגע במכירה, והיכן לקוחות פונים לנציג בשאלות שהיו יכולות
להיסגר לבד. שלושת השימושים נגזרו מהחיכוכים האלה ולא מרשימת יכולות טכנולוגית.

בבחירת המודל השוויתי את משפחת GPT-5.6 לפי תמחור OpenAI הרשמי נכון לאוגוסט 2026,
ובחנתי אותה מול הדרישות בפועל: תזמור כלים, עברית, ונפח. המסקנה הייתה שהמודל
החזק ביותר אינו הנכון ביותר — Terra מספק את היכולת הנדרשת בעלות נמוכה
משמעותית מ-Sol, בעוד ש-Luna מתאים לשכבת הסיווג.

בכתיבת הפרומפט התמקדתי במה שהבוט **אסור** לו לעשות. בסביבה שיש בה מרשמים ומידע
רפואי, כשלים אינם תשובה פחות מוצלחת אלא סיכון ממשי — ולכן אימות זהות, איסור
ייעוץ רפואי ואיסור המצאת מידע נכתבו כחוקים מפורשים ולא כהנחיות רכות.

*(כ-190 מילים)*

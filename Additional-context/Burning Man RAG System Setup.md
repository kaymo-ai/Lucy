Here is the burning\_man\_scenarios.json file.

This dataset is designed to test your RAG system's ability to synthesize information from multiple files (e.g., combining "Culture" with "Logistics"). You can use these as "Gold Standard" answers to evaluate your LLM's retrieval accuracy.

JSON

\[  
  {  
    "id": "scenario\_01",  
    "category": "Culture/Pranks",  
    "difficulty": "Easy",  
    "question": "It is 3:30 AM on Wednesday. Your campmate is frantically rushing to get on their bike because they heard Daft Punk is playing a secret set at the trash fence at 4:00 AM. What should you tell them?",  
    "answer": "You should tell them it is a prank. Daft Punk playing at the trash fence is the most famous running joke at Burning Man. They are not there, and your campmate will cycle miles into deep playa for nothing (though they might find a party of other fooled participants).",  
    "key\_concepts": \["Daft Punk", "Trash Fence", "Prank", "Burgin"\]  
  },  
  {  
    "id": "scenario\_02",  
    "category": "LNT/MOOP",  
    "difficulty": "Medium",  
    "question": "You are dancing at a sound camp and see a beautiful, pristine feather boa on the ground. It doesn't belong to anyone nearby. What is the correct course of action and why?",  
    "answer": "You must pick it up and put it in your trash/MOOP bag immediately. Do not wear it. Feathers are considered 'Public Enemy \#1' of MOOP (Matter Out of Place) because they shed easily and are difficult to clean up. Wearing it creates more MOOP.",  
    "key\_concepts": \["MOOP", "Feathers", "Leave No Trace", "Public Enemy \#1"\]  
  },  
  {  
    "id": "scenario\_03",  
    "category": "Ethics/Commerce",  
    "difficulty": "Hard",  
    "question": "A stranger walks into your camp and offers to trade you a handmade silver pendant in exchange for a cold beer. How should you respond based on the 10 Principles?",  
    "answer": "You should decline the 'trade' but offer the beer as a gift if you want to. Burning Man operates on a Gifting economy, not a Barter economy. Gifting must be unconditional; trading item-for-item is a transaction, which violates the principle of Decommodification/Gifting.",  
    "key\_concepts": \["Gifting", "Barter", "Decommodification", "10 Principles"\]  
  },  
  {  
    "id": "scenario\_04",  
    "category": "Logistics/Ticketing",  
    "difficulty": "Medium",  
    "question": "It is July, and you still don't have a ticket. Someone on Craigslist is offering to sell you a ticket for $2,500. Is this a safe buy?",  
    "answer": "No. First, tickets sold significantly above face value are against the community ethos. Second, third-party sales are high-risk for scams. The only 100% safe way to buy a ticket after the main sale is through STEP (Secure Ticket Exchange Program), which ensures face-value pricing and authentic tickets.",  
    "key\_concepts": \["STEP", "Scalping", "FOMO Sale", "Ticket Scams"\]  
  },  
  {  
    "id": "scenario\_05",  
    "category": "Safety/Infrastructure",  
    "difficulty": "Hard",  
    "question": "You are building a shade structure. You have a 10-inch nail and a hammer. Is this sufficient to anchor your carport in Black Rock City?",  
    "answer": "No. The playa floor is hard-packed alkaline silt that creates high resistance but can shatter or loosen under vibration. 10-inch nails will likely fail in 60+ mph winds. The recommended standard is using 14-18 inch lag bolts driven in with an impact driver, or rebar bent into candy canes.",  
    "key\_concepts": \["Lag Bolts", "Rebar", "Wind", "Anchoring"\]  
  },  
  {  
    "id": "scenario\_06",  
    "category": "Health/Physiology",  
    "difficulty": "Medium",  
    "question": "Your friend has been drinking water all day but has a headache, is cranky, and feels nauseous. They are confused because they are 'well hydrated.' What is likely happening?",  
    "answer": "They are likely suffering from hyponatremia (water intoxication) or electrolyte imbalance. Drinking water without replacing salts (electrolytes) dilutes the blood sodium levels. They need to eat something salty (pickles) or drink an electrolyte beverage immediately.",  
    "key\_concepts": \["Electrolytes", "Dehydration", "Salt", "Health"\]  
  },  
  {  
    "id": "scenario\_07",  
    "category": "Consent/Photography",  
    "difficulty": "Medium",  
    "question": "You see a naked participant riding a bike who looks incredibly artistic and happy. The lighting is perfect. Can you snap a quick photo?",  
    "answer": "No. You must ask for explicit consent first. Even in a public space, the community norm (and increasingly the rule) requires asking before taking portraits, especially involving nudity. Taking a photo without asking is a violation of the Consent principle.",  
    "key\_concepts": \["Consent", "Photography", "Nudity", "Privacy"\]  
  },  
  {  
    "id": "scenario\_08",  
    "category": "Environment/Grey Water",  
    "difficulty": "Easy",  
    "question": "You have a bucket of water left over from washing dishes. It's just water and biodegradable soap. Can you pour it on the road to keep the dust down?",  
    "answer": "No. This is 'Grey Water' and pouring it on the playa is illegal (considered MOOP). It creates mud and potential health hazards. You must either evaporate it in an evapotron/pond or pack it out in jugs.",  
    "key\_concepts": \["Grey Water", "MOOP", "Evaporation", "LNT"\]  
  }  
\]

### **Next Step: The Ingestion Script**

Since you have the **content** (Markdown files) and the **metadata** (JSON glossary and scenarios), the final step is the **Python script** to glue this all together.

I can write a script that:

1. **Chunks** the Markdown files (splitting by the \#\# headers to keep context intact).  
2. **Embeds** the chunks using a local model (like sentence-transformers / all-MiniLM-L6-v2) or an API (OpenAI/Gemini).  
3. **Stores** them in a local Vector DB. Since you mentioned "local file," I recommend **ChromaDB** (it runs locally, no server setup needed, saves to a folder).

Shall I generate that Python script for you?
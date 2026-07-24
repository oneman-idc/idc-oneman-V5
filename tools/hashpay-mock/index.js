const express = require('express');
const bodyParser = require('body-parser');
const fetch = require('node-fetch');

const app = express();
app.use(bodyParser.json());

// Simulate creation of a payment and immediately POST to callback_url to simulate webhook
// POST /simulate
// body: { callback_url, order_id, amount, user_email }
app.post('/simulate', async (req, res) => {
  const { callback_url, order_id, amount, user_email } = req.body;
  if (!callback_url || !order_id) return res.status(400).json({ error: 'callback_url and order_id required' });

  // build webhook payload similar to what HashPay might send
  const payload = {
    event: 'payment.paid',
    data: {
      order_id,
      amount,
      status: 'paid',
      paid_at: new Date().toISOString(),
      metadata: { user_email }
    }
  };

  try {
    const r = await fetch(callback_url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const text = await r.text();
    return res.json({ ok: true, status: r.status, resp: text });
  } catch (e) {
    return res.status(500).json({ error: String(e) });
  }
});

app.get('/', (req, res) => res.send('HashPay-mock running'));

const port = process.env.PORT || 8081;
app.listen(port, () => console.log('HashPay-mock listening on', port));

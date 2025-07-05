
import os, base64, datetime, smtplib, requests, threading, time
from flask import Flask, render_template_string, request, jsonify
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv
from email.message import EmailMessage
from ics import Calendar, Event
from email.utils import parseaddr
import pytz
import json
import uuid

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
app = Flask(__name__)

# In-memory storage for scheduled emails (use database in production)
scheduled_emails = {}

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Email Agent</title>
  <link href="https://cdn.jsdelivr.net/npm/tailwindcss@^2.2.19/dist/tailwind.min.css" rel="stylesheet">
  <style>
    .tab-button { @apply px-4 py-2 font-medium rounded-t-lg border-b-2 transition-colors; }
    .tab-button.active { @apply bg-blue-50 border-blue-500 text-blue-600; }
    .tab-button:not(.active) { @apply bg-gray-100 border-gray-300 text-gray-600 hover:bg-gray-200; }
    .tab-content { @apply hidden p-6 bg-white rounded-b-lg border border-gray-300; }
    .tab-content.active { @apply block; }
  </style>
</head>
<body class="min-h-screen bg-gradient-to-br from-blue-50 to-green-50 p-6">
  <div class="max-w-4xl mx-auto">
    <h1 class="text-3xl font-bold text-center text-blue-600 mb-8">📧  Email Reply Agent</h1>
    
    <!-- Tab Navigation -->
    <div class="flex space-x-1 mb-0">
      <button class="tab-button active" onclick="switchTab('reply')">Reply & Send</button>
      <button class="tab-button" onclick="switchTab('schedule')">Schedule Email</button>
      <button class="tab-button" onclick="switchTab('pending')">Pending Emails</button>
    </div>

    <!-- Reply Tab -->
    <div id="reply-tab" class="tab-content active">
      <form method="post" class="space-y-6">
        <!-- Select Email -->
        <div>
          <label for="msg_id" class="block text-sm font-medium text-gray-700">Select Email</label>
          <select id="msg_id" name="msg_id" class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500">
            <option value="">-- pick an email --</option>
            {% for e in emails %}
              <option value="{{ e.id }}" {% if selected and e.id == selected.id %}selected{% endif %}>
                {{ e.subject[:50] }}... — {{ e.sender }}
              </option>
            {% endfor %}
          </select>
        </div>

        <div class="flex justify-end">
          <button name="action" value="select" type="submit" class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition">
            Select Email
          </button>
        </div>

        <hr class="border-gray-300"/>

        <!-- Two Column Layout -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <!-- Left Column -->
          <div class="space-y-4">
            <!-- Sender Email -->
            <div>
              <label for="sender" class="block text-sm font-medium text-gray-700">Sender Email</label>
              <input id="sender" name="sender" type="email" required
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500"
                value="{{ selected.sender if selected else request.form.get('sender','') }}">
            </div>

            <!-- Subject -->
            <div>
              <label for="subject" class="block text-sm font-medium text-gray-700">Subject</label>
              <input id="subject" name="subject" type="text" required
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500"
                value="{% if selected %}Re: {{ selected.subject }}{% else %}{{ request.form.get('subject','') }}{% endif %}">
            </div>

            <!-- Meeting Details (Optional) -->
            <div class="border border-gray-200 rounded-lg p-4 bg-gray-50">
              <h3 class="text-sm font-medium text-gray-700 mb-3">📅 Meeting Details (Optional)</h3>
              
              <div class="space-y-3">
                <div>
                  <label for="meeting_time" class="block text-xs font-medium text-gray-600">Meeting Date & Time</label>
                  <input id="meeting_time" name="meeting_time" type="datetime-local"
                    class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm"
                    value="{{ request.form.get('meeting_time','') }}">
                </div>
                
                <div>
                  <label for="meeting_duration" class="block text-xs font-medium text-gray-600">Duration (hours)</label>
                  <input id="meeting_duration" name="meeting_duration" type="number" min="0.5" max="8" step="0.5" value="1"
                    class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm">
                </div>
                
                <div>
                  <label for="meeting_location" class="block text-xs font-medium text-gray-600">Location</label>
                  <input id="meeting_location" name="meeting_location" type="text" placeholder="Online/Physical address"
                    class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm"
                    value="{{ request.form.get('meeting_location', 'Online') }}">
                </div>
              </div>
            </div>
          </div>

          <!-- Right Column -->
          <div class="space-y-4">
            <!-- Email Body -->
            <div>
              <label for="email_body" class="block text-sm font-medium text-gray-700">Email Content</label>
              <textarea id="email_body" name="email_body" rows="6" required
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500">{{ selected.body if selected else request.form.get('email_body','') }}</textarea>
            </div>

            <!-- Reference -->
            <div>
              <label for="reference" class="block text-sm font-medium text-gray-700">Reference Context (Optional)</label>
              <textarea id="reference" name="reference" rows="3" placeholder="Add any additional context or instructions for the AI reply..."
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500">{{ request.form.get('reference','') }}</textarea>
            </div>
          </div>
        </div>

        <!-- Submit Button -->
        <div class="pt-4">
          <button name="action" value="send" type="submit"
            class="w-full px-6 py-3 bg-green-600 text-white rounded-md font-medium hover:bg-green-700 transition text-lg">
            🤖 Generate & Send Reply
          </button>
        </div>
      </form>
    </div>

    <!-- Schedule Tab -->
    <div id="schedule-tab" class="tab-content">
      <form method="post" class="space-y-6">
        <div class="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-6">
          <div class="flex">
            <div class="flex-shrink-0">
              <svg class="h-5 w-5 text-yellow-400" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
              </svg>
            </div>
            <div class="ml-3">
              <p class="text-sm text-yellow-700">
                <strong>Schedule Email:</strong> Set when you want this email to be sent automatically.
              </p>
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div class="space-y-4">
            <div>
              <label for="sched_sender" class="block text-sm font-medium text-gray-700">Recipient Email</label>
              <input id="sched_sender" name="sched_sender" type="email" required
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500">
            </div>

            <div>
              <label for="sched_subject" class="block text-sm font-medium text-gray-700">Subject</label>
              <input id="sched_subject" name="sched_subject" type="text" required
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500">
            </div>

            <div>
              <label for="schedule_time" class="block text-sm font-medium text-gray-700">Send Date & Time</label>
              <input id="schedule_time" name="schedule_time" type="datetime-local" required
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500">
            </div>
          </div>

          <div class="space-y-4">
            <div>
              <label for="sched_email_body" class="block text-sm font-medium text-gray-700">Email Content</label>
              <textarea id="sched_email_body" name="sched_email_body" rows="6" required
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500"></textarea>
            </div>

            <div>
              <label for="sched_reference" class="block text-sm font-medium text-gray-700">Reference Context (Optional)</label>
              <textarea id="sched_reference" name="sched_reference" rows="2"
                class="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500"></textarea>
            </div>
          </div>
        </div>

        <div class="pt-4">
          <button name="action" value="schedule" type="submit"
            class="w-full px-6 py-3 bg-orange-600 text-black rounded-md font-medium hover:bg-orange-700 transition text-lg">
            ⏰ Schedule Email
          </button>
        </div>
      </form>
    </div>

    <!-- Pending Emails Tab -->
    <div id="pending-tab" class="tab-content">
      <div class="space-y-4">
        <h3 class="text-lg font-semibold text-gray-800">Scheduled Emails</h3>
        <div id="pending-emails-list">
          <!-- Will be populated by JavaScript -->
        </div>
        <button onclick="loadPendingEmails()" class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition">
          Refresh List
        </button>
      </div>
    </div>

    {% if result %}
      <div class="mt-6 p-4 {% if 'Error' in result %}bg-red-50 border-l-4 border-red-400 text-red-800{% else %}bg-green-50 border-l-4 border-green-400 text-green-800{% endif %} rounded-md">
        <strong>Result:</strong> {{ result }}
      </div>
    {% endif %}
  </div>

  <script>
    function switchTab(tab) {
      // Hide all tabs
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-button').forEach(b => b.classList.remove('active'));
      
      // Show selected tab
      document.getElementById(tab + '-tab').classList.add('active');
      event.target.classList.add('active');
      
      if (tab === 'pending') {
        loadPendingEmails();
      }
    }

    // Auto-fill form when email is selected
    document.addEventListener('DOMContentLoaded', function() {
      const emailSelect = document.getElementById('msg_id');
      if (emailSelect) {
        emailSelect.addEventListener('change', function() {
          const selectedOption = this.options[this.selectedIndex];
          if (selectedOption.value) {
            // Auto-submit the form to load the selected email
            const form = this.closest('form');
            const actionInput = document.createElement('input');
            actionInput.type = 'hidden';
            actionInput.name = 'action';
            actionInput.value = 'select';
            form.appendChild(actionInput);
            form.submit();
          }
        });
      }

      // Set minimum datetime to current time
      const now = new Date();
      now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
      const minDateTime = now.toISOString().slice(0, 16);
      
      const scheduleTimeInput = document.getElementById('schedule_time');
      const meetingTimeInput = document.getElementById('meeting_time');
      
      if (scheduleTimeInput) scheduleTimeInput.min = minDateTime;
      if (meetingTimeInput) meetingTimeInput.min = minDateTime;
    });

    function loadPendingEmails() {
      fetch('/api/pending-emails')
        .then(response => response.json())
        .then(data => {
          const container = document.getElementById('pending-emails-list');
          if (data.length === 0) {
            container.innerHTML = '<p class="text-gray-500 italic">No scheduled emails</p>';
            return;
          }
          
          container.innerHTML = data.map(email => `
            <div class="border border-gray-200 rounded-lg p-4 bg-white">
              <div class="flex justify-between items-start">
                <div class="flex-1">
                  <h4 class="font-medium text-gray-800">${email.subject}</h4>
                  <p class="text-sm text-gray-600">To: ${email.recipient}</p>
                  <p class="text-sm text-gray-600">Scheduled: ${new Date(email.send_time).toLocaleString()}</p>
                  <p class="text-xs text-gray-500 mt-2">${email.content.substring(0, 100)}...</p>
                </div>
                <button onclick="cancelScheduledEmail('${email.id}')" 
                  class="px-3 py-1 bg-red-500 text-white rounded text-sm hover:bg-red-600 transition">
                  Cancel
                </button>
              </div>
            </div>
          `).join('');
        })
        .catch(error => {
          console.error('Error loading pending emails:', error);
          document.getElementById('pending-emails-list').innerHTML = '<p class="text-red-500">Error loading scheduled emails</p>';
        });
    }

    function cancelScheduledEmail(emailId) {
      if (confirm('Are you sure you want to cancel this scheduled email?')) {
        fetch('/api/cancel-email', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email_id: emailId })
        })
        .then(response => response.json())
        .then(data => {
          if (data.success) {
            loadPendingEmails();
          } else {
            alert('Error canceling email: ' + data.error);
          }
        })
        .catch(error => {
          console.error('Error:', error);
          alert('Error canceling email');
        });
      }
    }
  </script>
</body>
</html>
"""

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json','w') as f: 
            f.write(creds.to_json())
    return build('gmail','v1',credentials=creds)

def decode_body(payload):
    if 'parts' in payload:
        for p in payload['parts']:
            if p.get('mimeType','').startswith('text/plain'):
                d = p['body'].get('data')
                if d:
                    return base64.urlsafe_b64decode(d).decode('utf-8','ignore')
    d = payload.get('body',{}).get('data')
    return base64.urlsafe_b64decode(d).decode('utf-8','ignore') if d else ''

def fetch_emails():
    try:
        svc = get_service()
        results = svc.users().messages().list(
            userId='me',
            labelIds=['INBOX'],
            maxResults=50
        ).execute()

        out = []
        for m in results.get('messages', []):
            msg = svc.users().messages().get(
                userId='me',
                id=m['id'],
                format='full'
            ).execute()
            hdrs = msg['payload'].get('headers', [])

            # Filter for Gmail-sent messages
            if not any(h['name'].lower() in ('mailed-by','return-path') and 'gmail.com' in h['value'] for h in hdrs):
                continue

            raw_from = next((h['value'] for h in hdrs if h['name'] == 'From'), '')
            _, clean_email = parseaddr(raw_from)

            subject = next((h['value'] for h in hdrs if h['name'] == 'Subject'), '')
            body = decode_body(msg['payload'])

            e = type('E', (), {
                'id': m['id'],
                'sender': clean_email,
                'subject': subject,
                'body': body
            })()
            out.append(e)

        return out
    except Exception as e:
        print(f"Error fetching emails: {e}")
        return []

def generate_reply(email_body, reference="", meeting_time=None):
    """Generate AI reply with proper context and meeting details"""
    try:
        prompt_parts = ["You are a professional and polite email assistant."]
        
        if reference.strip():
            prompt_parts.append(f"Additional context: {reference}")
        
        if meeting_time:
            # Format meeting time properly
            ist_tz = pytz.timezone('Asia/Kolkata')
            if meeting_time.tzinfo is None:
                meeting_time = ist_tz.localize(meeting_time)
            
            formatted_date = meeting_time.strftime("%A, %B %d, %Y")
            formatted_time = meeting_time.strftime("%I:%M %p %Z")
            
            prompt_parts.append(f"There is a meeting scheduled for {formatted_date} at {formatted_time}.")
            prompt_parts.append("Include meeting details in your response naturally.")
        
        prompt_parts.append(f"Reply professionally to this email:\n\n{email_body}")
        prompt_parts.append("\nGenerate a helpful and professional reply:")
        
        prompt = "\n".join(prompt_parts)
        
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
            json={"contents":[{"parts":[{"text":prompt}]}]}
        )
        resp.raise_for_status()
        
        reply = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return reply
        
    except Exception as e:
        print(f"Error generating reply: {e}")
        return f"Thank you for your email. I will get back to you soon.\n\nBest regards"

def create_calendar_invite(subject, start_time, duration_hours=1, location="Online"):
    """Create proper calendar invite with timezone handling"""
    try:
        cal = Calendar()
        cal.method = "REQUEST"
        
        event = Event()
        event.name = subject
        
        # Ensure timezone awareness
        ist_tz = pytz.timezone('Asia/Kolkata')
        if start_time.tzinfo is None:
            start_time = ist_tz.localize(start_time)
        
        event.begin = start_time
        event.end = start_time + datetime.timedelta(hours=duration_hours)
        event.location = location
        
        # Add organizer
        your_email = os.getenv("MY_AGENT_EMAIL")
        your_name = os.getenv("MY_AGENT_NAME", "Email Agent")
        event.organizer = f"mailto:{your_email}"
        
        cal.events.add(event)
        return cal.serialize().encode("utf-8")
        
    except Exception as e:
        print(f"Error creating calendar invite: {e}")
        return None

def send_email(to, subject, body, meeting_time=None, duration=1, location="Online"):
    """Send email with optional calendar invite"""
    try:
        your_email = os.getenv("MY_AGENT_EMAIL")
        your_password = os.getenv("MY_AGENT_PASS")

        if not your_email or not your_password:
            raise Exception("Email credentials not configured")

        msg = EmailMessage()
        msg["From"] = your_email
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        # Add calendar invite if meeting time is provided
        if meeting_time:
            ics_content = create_calendar_invite(subject, meeting_time, duration, location)
            if ics_content:
                msg.add_attachment(
                    ics_content,
                    maintype='text',
                    subtype='calendar',
                    filename="meeting.ics"
                )

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(your_email, your_password)
            smtp.send_message(msg)
            
        return True
        
    except Exception as e:
        print(f"Error sending email: {e}")
        raise e

def log_email(sender, subject, body, reply):
    """Log email interactions"""
    try:
        log_path = os.path.join(os.path.expanduser("~"), "Downloads", "email_replies_log.txt")
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"\n{timestamp} - Reply to: {sender}\nSubject: {subject}\nReply: {reply}\n" + "="*50 + "\n"
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
            
    except Exception as e:
        print(f"Error logging email: {e}")

def schedule_email_sender(email_id, recipient, subject, content, send_time):
    """Background thread to send scheduled email"""
    def send_scheduled():
        try:
            now = datetime.datetime.now()
            sleep_seconds = (send_time - now).total_seconds()
            
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            
            # Check if email was cancelled
            if email_id not in scheduled_emails:
                return
                
            # Send the email
            send_email(recipient, subject, content)
            
            # Log the email
            log_email(recipient, subject, "", content)
            
            # Remove from scheduled list
            if email_id in scheduled_emails:
                del scheduled_emails[email_id]
                
            print(f"Scheduled email sent to {recipient}")
            
        except Exception as e:
            print(f"Error sending scheduled email: {e}")
            # Remove from scheduled list even on error
            if email_id in scheduled_emails:
                del scheduled_emails[email_id]
    
    thread = threading.Thread(target=send_scheduled)
    thread.daemon = True
    thread.start()

@app.route("/api/pending-emails")
def get_pending_emails():
    """API endpoint to get scheduled emails"""
    pending = []
    for email_id, email_data in scheduled_emails.items():
        pending.append({
            'id': email_id,
            'recipient': email_data['recipient'],
            'subject': email_data['subject'],
            'content': email_data['content'],
            'send_time': email_data['send_time'].isoformat()
        })
    
    # Sort by send time
    pending.sort(key=lambda x: x['send_time'])
    return jsonify(pending)

@app.route("/api/cancel-email", methods=["POST"])
def cancel_scheduled_email():
    """API endpoint to cancel scheduled email"""
    try:
        data = request.get_json()
        email_id = data.get('email_id')
        
        if email_id in scheduled_emails:
            del scheduled_emails[email_id]
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Email not found'})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route("/", methods=["GET", "POST"])
def index():
    emails = fetch_emails()
    selected = None
    result = None
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "select":
            # Select email for reply
            msg_id = request.form.get("msg_id", "")
            selected = next((e for e in emails if e.id == msg_id), None)
            if selected:
                print(f"Selected email: {selected.subject} from {selected.sender}")  # Debug log
            
        elif action == "send":
            # Send immediate reply
            try:
                sender = request.form.get("sender", "").strip()
                subject = request.form.get("subject", "").strip()
                email_body = request.form.get("email_body", "").strip()
                reference = request.form.get("reference", "").strip()
                meeting_time_str = request.form.get("meeting_time", "").strip()
                duration = float(request.form.get("meeting_duration", 1))
                location = request.form.get("meeting_location", "Online").strip()
                
                if not sender or not subject or not email_body:
                    result = "Error: Please fill in all required fields"
                else:
                    # Parse meeting time
                    meeting_time = None
                    if meeting_time_str:
                        meeting_time = datetime.datetime.fromisoformat(meeting_time_str)
                    
                    # Generate AI reply
                    reply = generate_reply(email_body, reference, meeting_time)
                    
                    # Send email
                    send_email(sender, f"Re: {subject}", reply, meeting_time, duration, location)
                    
                    # Log the interaction
                    log_email(sender, subject, email_body, reply)
                    
                    result = f"Reply sent successfully to {sender}!"
                    
            except Exception as e:
                result = f"Error: {str(e)}"
                
        elif action == "schedule":
            # Schedule email for later
            try:
                recipient = request.form.get("sched_sender", "").strip()
                subject = request.form.get("sched_subject", "").strip()
                email_body = request.form.get("sched_email_body", "").strip()
                reference = request.form.get("sched_reference", "").strip()
                schedule_time_str = request.form.get("schedule_time", "").strip()
                
                if not recipient or not subject or not email_body or not schedule_time_str:
                    result = "Error: Please fill in all required fields for scheduling"
                else:
                    send_time = datetime.datetime.fromisoformat(schedule_time_str)
                    
                    # Check if time is in the future
                    if send_time <= datetime.datetime.now():
                        result = "Error: Scheduled time must be in the future"
                    else:
                        # Generate AI content
                        content = generate_reply( reference)
                        content = email_body
                        
                        # Create unique ID for scheduled email
                        email_id = str(uuid.uuid4())
                        
                        # Store scheduled email
                        scheduled_emails[email_id] = {
                            'recipient': recipient,
                            'subject': subject,
                            'content': content,
                            'send_time': send_time
                        }
                        
                        # Start background scheduler
                        schedule_email_sender(email_id, recipient, subject, content, send_time)
                        
                        result = f"Email scheduled successfully for {send_time.strftime('%Y-%m-%d %H:%M')}!"
                        
            except Exception as e:
                result = f"Error scheduling email: {str(e)}"
    
    return render_template_string(HTML, emails=emails, selected=selected, result=result)

if __name__ == "__main__":
    print("Starting Enhanced Email Reply Agent...")
    print("Features:")
    print("✓ Immediate email replies with AI")
    print("✓ Scheduled email sending")
    print("✓ Calendar invites with proper timezone")
    print("✓ Reference context support")
    print("✓ Email logging")
    app.run(debug=True)

    # token : {"token": "ya29.a0AS3H6NyKy8G6CzFKNBJcVKDueS0BSrBw-3DWHoS1rStNrF8sCVj2pvfnqk5YtB5MEdOzviu2iHsPmrAVAJjF2jOqUBFBQA5Iy5odWAnfm7rUgmf82QS05vADKXDFzw5Mj0HARulLT47qu3uAsvI0R442X16UqJu5ZgXw7nixDQaCgYKAVESAQ4SFQHGX2Mi-ELuv3ori9p6Lr7qiQG5cA0177", "refresh_token": "1//0gtPNHA7X1J3yCgYIARAAGBASNwF-L9Ir8PNz9jDX1J9BotbznvdiZwwZHdadgOvi0jAmWOx4FtE3yYnnyy67l2abKNYqaBlt8Rk", "token_uri": "https://oauth2.googleapis.com/token", "client_id": "849164736699-a8jri63fg1dom1fiei3trj8mc82eeosu.apps.googleusercontent.com", "client_secret": "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", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"], "universe_domain": "googleapis.com", "account": "", "expiry": "2025-06-27T08:38:04.924450Z"}
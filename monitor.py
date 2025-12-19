import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objs as go
import pandas as pd
import os
from datetime import datetime
from collections import defaultdict

# Configuration
LOG_DIRECTORY = ''

# Color palette for different masters
COLOR_PALETTE = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8']

# Initialize Dash app
app = dash.Dash(__name__)
app.title = "ESP32 Data Viewer"

# App layout
app.layout = html.Div([
    html.Div([
        html.H1("ESP32 Multicast Logger Viewer", 
                style={'textAlign': 'center', 'color': '#2C3E50', 'marginBottom': '10px'}),
        html.P("Log file viewer for ESP32 photocell data",
               style={'textAlign': 'center', 'color': '#7F8C8D', 'marginBottom': '30px'})
    ]),
    
    html.Div([
        html.Div([
            html.Label("Select Log File:", style={'fontWeight': 'bold', 'fontSize': '16px'}),
            dcc.Dropdown(
                id='file-dropdown',
                options=[],
                placeholder="Choose a log file...",
                style={'width': '100%'}
            ),
        ], style={'width': '70%', 'display': 'inline-block', 'verticalAlign': 'middle'}),
        
        html.Div([
            html.Button('Refresh Files', id='refresh-button', n_clicks=0,
                       style={
                           'backgroundColor': '#3498DB',
                           'color': 'white',
                           'border': 'none',
                           'padding': '10px 20px',
                           'fontSize': '16px',
                           'borderRadius': '5px',
                           'cursor': 'pointer',
                           'marginLeft': '10px'
                       })
        ], style={'width': '28%', 'display': 'inline-block', 'verticalAlign': 'middle', 'textAlign': 'right'}),
    ], style={'marginBottom': '20px', 'padding': '20px', 'backgroundColor': '#ECF0F1', 'borderRadius': '10px'}),
    
    html.Div(id='file-info', style={'marginBottom': '20px', 'padding': '15px', 
                                     'backgroundColor': '#E8F8F5', 'borderRadius': '5px',
                                     'border': '1px solid #A9DFBF'}),
    
    dcc.Graph(id='light-graph', style={'marginBottom': '30px'}),
    
    dcc.Graph(id='duration-graph'),
    
    dcc.Interval(
        id='interval-component',
        interval=5*1000,
        n_intervals=0
    )
], style={'padding': '20px', 'maxWidth': '1400px', 'margin': '0 auto', 'fontFamily': 'Arial, sans-serif'})


def get_log_files():
    """Get list of log files from directory"""
    try:
        files = [f for f in os.listdir(LOG_DIRECTORY) if f.startswith('esp32_log_') and f.endswith('.csv')]
        files.sort(reverse=True)
        return files
    except Exception as e:
        print(f"Error reading log directory: {e}")
        return []


def parse_log_file(filename):
    """Parse CSV log file and return dataframe"""
    try:
        filepath = os.path.join(LOG_DIRECTORY, filename)
        df = pd.read_csv(filepath)
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
        return df
    except Exception as e:
        print(f"Error parsing log file: {e}")
        return None


def get_master_color(master_ip, master_colors):
    """Assign a unique color to each master device"""
    if master_ip not in master_colors:
        master_colors[master_ip] = COLOR_PALETTE[len(master_colors) % len(COLOR_PALETTE)]
    return master_colors[master_ip]


@app.callback(
    Output('file-dropdown', 'options'),
    [Input('refresh-button', 'n_clicks'),
     Input('interval-component', 'n_intervals')]
)
def update_file_list(n_clicks, n_intervals):
    """Update the list of available log files"""
    files = get_log_files()
    
    options = []
    for f in files:
        try:
            timestamp_str = f.replace('esp32_log_', '').replace('.csv', '')
            dt = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
            label = dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            label = f
        
        options.append({'label': label, 'value': f})
    
    return options


@app.callback(
    [Output('light-graph', 'figure'),
     Output('duration-graph', 'figure'),
     Output('file-info', 'children')],
    [Input('file-dropdown', 'value'),
     Input('interval-component', 'n_intervals')]
)
def update_graphs(selected_file, n_intervals):
    """Update both graphs based on selected file"""
    
    if not selected_file:
        empty_fig = go.Figure()
        empty_fig.update_layout(
            title="No file selected",
            xaxis={'visible': False},
            yaxis={'visible': False},
            annotations=[{
                'text': 'Please select a log file from the dropdown above',
                'xref': 'paper',
                'yref': 'paper',
                'showarrow': False,
                'font': {'size': 20, 'color': '#95A5A6'}
            }]
        )
        info = html.Div("Select a log file to view data", style={'textAlign': 'center', 'color': '#7F8C8D'})
        return empty_fig, empty_fig, info
    
    # Parse the log file
    df = parse_log_file(selected_file)
    
    if df is None or df.empty:
        empty_fig = go.Figure()
        empty_fig.update_layout(
            title="Error loading file or no data",
            xaxis={'visible': False},
            yaxis={'visible': False}
        )
        info = html.Div("Error loading file or no data available", 
                       style={'textAlign': 'center', 'color': '#E74C3C'})
        return empty_fig, empty_fig, info
    
    total_records = len(df)
    unique_masters = df['Master_IP'].nunique()
    start_time = df['Timestamp'].min()
    end_time = df['Timestamp'].max()
    duration = (end_time - start_time).total_seconds()
    
    info = html.Div([
        html.Strong("File: "),
        html.Span(selected_file),
        html.Br(),
        html.Strong("Records: "),
        html.Span(f"{total_records:,}"),
        html.Span(" | ", style={'margin': '0 10px'}),
        html.Strong("Unique Masters: "),
        html.Span(str(unique_masters)),
        html.Span(" | ", style={'margin': '0 10px'}),
        html.Strong("Duration: "),
        html.Span(f"{duration:.1f} seconds"),
        html.Span(" | ", style={'margin': '0 10px'}),
        html.Strong("Time Range: "),
        html.Span(f"{start_time.strftime('%H:%M:%S')} - {end_time.strftime('%H:%M:%S')}")
    ])
    
    master_colors = {}
    unique_ips = df['Master_IP'].unique()
    for ip in unique_ips:
        get_master_color(ip, master_colors)

    # Graph 1
    light_fig = go.Figure()
    
    df['Seconds'] = (df['Timestamp'] - df['Timestamp'].min()).dt.total_seconds()
    df_sorted = df.sort_values('Seconds').reset_index(drop=True)
    
    if len(df_sorted) > 0:
        i = 0
        while i < len(df_sorted):
            current_master = df_sorted.loc[i, 'Master_IP']
            color = master_colors[current_master]
            
            j = i
            while j < len(df_sorted) and df_sorted.loc[j, 'Master_IP'] == current_master:
                j += 1
            
            segment = df_sorted.iloc[i:j]
            
            light_fig.add_trace(go.Scatter(
                x=segment['Seconds'],
                y=segment['Light_Value'],
                mode='lines',
                name=f"Master: {current_master.split('.')[-1]}",
                line=dict(color=color, width=2),
                hovertemplate=f'<b>Master:</b> {current_master}<br><b>Time:</b> %{{x:.1f}}s<br><b>Light:</b> %{{y}}<br><extra></extra>',
                showlegend=True if i == 0 or current_master not in [df_sorted.loc[k, 'Master_IP'] for k in range(i)] else False
            ))
            
            i = j
    
    light_fig.update_layout(
        title="Photocell Light Values Over Time (Current Master Only)",
        xaxis_title="Time (seconds from start)",
        yaxis_title="Light Value (ADC)",
        yaxis_range=[0, 4095],
        hovermode='closest',
        template='plotly_white',
        height=600,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    light_fig.update_yaxes(range=[0, 4095])
    
    # Graph 2
    master_duration = defaultdict(float)
    
    df_sorted = df.sort_values('Timestamp').reset_index(drop=True)
    
    for i in range(len(df_sorted) - 1):
        current_master = df_sorted.loc[i, 'Master_IP']
        current_time = df_sorted.loc[i, 'Timestamp']
        next_time = df_sorted.loc[i + 1, 'Timestamp']
        
        time_diff = (next_time - current_time).total_seconds()
        
        if time_diff < 5.0:
            master_duration[current_master] += time_diff    

    if master_duration:
        ips = list(master_duration.keys())
        durations = [master_duration[ip] for ip in ips]
        short_ips = [ip.split('.')[-1] for ip in ips]
        colors_list = [master_colors[ip] for ip in ips]
        
        duration_fig = go.Figure(data=[
            go.Bar(
                x=short_ips,
                y=durations,
                marker_color=colors_list,
                text=[f"{d:.1f}s" for d in durations],
                textposition='outside',
                hovertemplate='<b>Device:</b> %{x}<br><b>Duration:</b> %{y:.2f}s<br><extra></extra>'
            )
        ])
        
        duration_fig.update_layout(
            title="Master Device Active Duration",
            xaxis_title="Device IP (last octet)",
            yaxis_title="Duration (seconds)",
            template='plotly_white',
            height=600,
            showlegend=False
        )
    else:
        duration_fig = go.Figure()
        duration_fig.update_layout(
            title="Master Device Active Duration",
            xaxis_title="Device IP",
            yaxis_title="Duration (seconds)",
            template='plotly_white',
            height=600
        )
    
    return light_fig, duration_fig, info


if __name__ == '__main__':
    print("=" * 60)
    print("ESP32 Data Viewer - Web Interface")
    print("=" * 60)
    print(f"Log Directory: {LOG_DIRECTORY}")
    print(f"Starting web server...")
    print(f"Open browser to: http://localhost:8050")
    print("=" * 60)
    
    app.run(debug=True, host='0.0.0.0', port=8050)

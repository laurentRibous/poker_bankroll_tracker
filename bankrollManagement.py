import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
from datetime import datetime
import os

# Configuration de la base de données
DB_PATH = os.path.join(os.path.expanduser("~"), "poker_bankroll.db")

def get_db_connection():
    """Create a new database connection for each function call"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return conn

def init_db():
    """Initialize database tables"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Table des rooms
    c.execute('''
    CREATE TABLE IF NOT EXISTS rooms (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        initial_bankroll REAL,
        init_date TEXT
    )''')
    
    # Drop table sessions if it exists (for development purposes)
    # Supprime la table 'sessions' si elle existe (pour le développement)
    # c.execute("DROP TABLE IF EXISTS sessions") # Garder ceci commenté pour la production

    # Table des sessions
    c.execute('''
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY,
        room_id INTEGER,
        date TEXT,
        tournaments INTEGER,
        cashflow REAL,
        bankroll REAL,
        FOREIGN KEY(room_id) REFERENCES rooms(id),
        UNIQUE(room_id, date) -- Pour permettre les upserts
    )''')
    
    # Table d'historique des modifications
    c.execute('''
    CREATE TABLE IF NOT EXISTS edits_history (
        id INTEGER PRIMARY KEY,
        table_name TEXT,
        record_id INTEGER,
        old_value TEXT,
        new_value TEXT,
        edit_time TEXT,
        user_action TEXT
    )''')
    
    conn.commit()
    conn.close()

def get_room_bankroll(room_id, date=None):
    """Calcule la bankroll actuelle"""
    conn = get_db_connection()
    
    # Dernière session
    last_session = conn.execute('''
    SELECT bankroll FROM sessions 
    WHERE room_id = ? 
    ORDER BY date DESC LIMIT 1
    ''', (room_id,)).fetchone()
    
    conn.close()
    
    return last_session[0] if last_session else 0

def setup_rooms():
    st.header("Configuration Initiale")
    
    # Ajout de 'FDJ' et 'Coin poker' aux rooms par défaut
    default_rooms = ["Winamax", "PokerStars", "Betclic", "PMU", "PartyPoker", "FDJ", "Coin poker", "Unibet"]
    
    for room in default_rooms:
        if st.checkbox(f"Ajouter {room}", key=f"init_{room}"):
            col1, col2 = st.columns(2)
            with col1:
                br = st.number_input(f"Bankroll initiale {room} €", min_value=0.0, key=f"br_{room}")
            with col2:
                init_date = st.date_input(f"Date d'initialisation {room}", key=f"date_{room}")
            
            if st.button(f"Valider {room}"):
                conn = get_db_connection()
                c = conn.cursor()
                try:
                    # Insertion de la room
                    c.execute("INSERT INTO rooms (name, initial_bankroll, init_date) VALUES (?, ?, ?)", 
                             (room, br, str(init_date)))
                    room_id = c.lastrowid
                    
                    # Insertion de la session initiale
                    c.execute('''
                    INSERT INTO sessions (room_id, date, tournaments, cashflow, bankroll)
                    VALUES (?, ?, 0, 0, ?)
                    ''', (room_id, str(init_date), br))
                    
                    conn.commit()
                    st.success(f"{room} ajouté avec sa session initiale!")
                except sqlite3.IntegrityError:
                    st.warning(f"{room} existe déjà!")
                finally:
                    conn.close()

def add_session():
    st.header("Nouvelle Session")
    
    conn = get_db_connection()
    rooms = conn.execute("SELECT id, name, initial_bankroll, init_date FROM rooms").fetchall()
    conn.close() # Close connection used for fetching rooms

    if not rooms:
        st.warning("Configurez d'abord vos rooms avant d'ajouter une session.")
        return
    
    # Affichage des rooms avec leurs dates d'initialisation
    st.subheader("Rooms configurées")
    for room in rooms:
        st.write(f"**{room[1]}** - {room[2]:.2f}€ (depuis le {room[3]})")
    
    room_choice = st.selectbox("Room", [r[1] for r in rooms], key="add_session_room_select")
    room_id = [r[0] for r in rooms if r[1] == room_choice][0]
    
    date = st.date_input("Date", key="add_session_date_input")
    tournaments = st.number_input("Nombre de tournois", min_value=0, key="add_session_tournaments")
    cashflow = st.number_input("Dépôt/Retrait (€)", value=0.0, 
                              help="Entrez un montant positif pour un dépôt, négatif pour un retrait",
                              key="add_session_cashflow")
    bankroll = st.number_input(f"Bankroll {room_choice} après session €", min_value=0.0, key="add_session_bankroll")
    
    if st.button("Enregistrer la session", key="add_session_submit"):
        conn_write = get_db_connection() # Use a new connection for writing operations
        c_write = conn_write.cursor()
        
        try:
            st.info("Tentative d'enregistrement de la session dans la base de données...")
            
            # Tentative d'upsert
            c_write.execute('''
            INSERT INTO sessions (room_id, date, tournaments, cashflow, bankroll)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(room_id, date) DO UPDATE SET
                tournaments = tournaments + excluded.tournaments,
                cashflow = cashflow + excluded.cashflow,
                bankroll = excluded.bankroll
            ''', (room_id, str(date), tournaments, cashflow, bankroll))
            
            session_id = c_write.lastrowid
            action_type = "CREATE"
            
            if session_id is None: # This means an update occurred (ON CONFLICT DO UPDATE)
                # Retrieve the ID of the existing row after update
                existing_session_query = conn_write.execute(
                    "SELECT id FROM sessions WHERE room_id = ? AND date = ?", 
                    (room_id, str(date))
                ).fetchone()
                if existing_session_query:
                    session_id = existing_session_query[0]
                    action_type = "UPDATE"
                    st.info(f"Session existante mise à jour (ID: {session_id}).")
                else:
                    st.error("Erreur critique: Session mise à jour mais ID introuvable pour l'historique!")
                    # This case should ideally not happen if the ON CONFLICT logic is correct
                    raise ValueError("Session ID not found after update.")
            else:
                st.info(f"Nouvelle session insérée (ID: {session_id}).")
            
            # Enregistrement dans l'historique
            # old_value: Pour la simplicité lors du débogage, nous indiquons si c'était une création ou une mise à jour.
            #            La récupération de la valeur réelle précédente est plus complexe pour ON CONFLICT.
            old_value_str = 'INITIAL_INSERT' if action_type == 'CREATE' else 'PREVIOUS_STATE_NOT_CAPTURED'
            
            # new_value: Stocke le nouvel état dans un format de chaîne de dictionnaire lisible
            new_value_dict = {
                'date': str(date),
                'tournaments': tournaments,
                'cashflow': cashflow,
                'bankroll': bankroll
            }

            c_write.execute('''
            INSERT INTO edits_history (table_name, record_id, old_value, new_value, edit_time, user_action)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ''', ('sessions', session_id, old_value_str, str(new_value_dict), action_type))
            
            conn_write.commit()
            st.success("Session enregistrée avec succès !")
            st.rerun() # Force un re-rendu pour vider le formulaire et afficher les changements
            
        except Exception as e:
            conn_write.rollback()
            st.error(f"Erreur lors de l'enregistrement de la session: {e}. Veuillez vérifier les logs de la console pour plus de détails.")
            print(f"DEBUGGING ERROR IN add_session: {e}") # Ceci s'affichera dans la console Streamlit
        finally:
            conn_write.close()

def process_room_data(sessions_df, initial_bankroll, init_date, end_date):
    """Traite les données d'une room pour calculer les profits journaliers."""
    # Créer une plage de dates complète
    full_date_range = pd.date_range(start=init_date, end=end_date, freq='D')

    # Réindexer et remplir les jours manquants
    df = sessions_df.set_index('date').reindex(full_date_range)

    # Remplir la bankroll avec la dernière valeur connue
    # Cas initial : si la première date n'a pas de session, on part de la bankroll initiale.
    if pd.isna(df['bankroll'].iloc[0]):
         df['bankroll'].iloc[0] = initial_bankroll
    df['bankroll'] = df['bankroll'].ffill()

    # Remplir les autres valeurs avec 0
    df['cashflow'] = df['cashflow'].fillna(0)
    df['tournaments'] = df['tournaments'].fillna(0)

    # Calculer la bankroll de la veille
    shifted_bankroll = df['bankroll'].shift(1)
    shifted_bankroll.iloc[0] = initial_bankroll # Le premier jour est comparé à la BR initiale

    # Calcul du profit pur
    df['pure_profit'] = (df['bankroll'] - df['cashflow']) - shifted_bankroll

    return df.reset_index().rename(columns={'index': 'date'})
    
def room_stats():
    st.header("Statistiques par Room")
    
    conn = get_db_connection()
    rooms = conn.execute("SELECT id, name, initial_bankroll, init_date FROM rooms").fetchall()
    
    if not rooms:
        st.warning("Configurez d'abord vos rooms")
        conn.close()
        return
    
    room_choice = st.selectbox("Choisir une room", [r[1] for r in rooms], key="room_select")
    room_data = next((r for r in rooms if r[1] == room_choice), None)
    room_id, room_name, initial_br, init_date_str = room_data
    init_date = pd.to_datetime(init_date_str) # Conversion en Timestamp
    
    # 1. Récupérer les données brutes pour la room sélectionnée
    query = f'''
    SELECT 
        date,
        bankroll,
        cashflow,
        tournaments
    FROM sessions
    WHERE room_id = {room_id} AND date >= '{init_date_str}'
    ORDER BY date
    '''
    
    raw_data = pd.read_sql(query, conn, parse_dates=['date'])
    conn.close()

    # 2. Traiter les données pour combler les manques et calculer le profit de manière robuste
    today = pd.Timestamp.now().floor('D')
    data = process_room_data(raw_data, initial_br, init_date, today)

    if not data.empty:
        tab1, tab2, tab3 = st.tabs(["Bankroll", "Profit net", "Tournois"])
        
        with tab1:
            # Période pour la Bankroll : Uniquement Journalier
            period_br = st.selectbox("Période de la Bankroll", ["Journalier"], key=f"room_br_period_{room_id}")
            
            # Le graphique de bankroll utilise toujours les données journalières
            fig = px.line(data, x='date', y='bankroll',
                          title="Évolution de la bankroll",
                          labels={'bankroll': 'Bankroll (€)', 'date': 'Date'})
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Évolution de la bankroll au fil du temps (journalier).")
        
        with tab2:
            # Période pour le Profit : Toutes les options
            period_profit = st.selectbox("Période du Profit", ["Journalier", "Hebdomadaire", "Mensuel", "Annuel"], key=f"room_profit_period_{room_id}")
            
            # Recalculer les données groupées en fonction de period_profit
            grouped_profit = data.set_index('date')
            if period_profit == "Hebdomadaire":
                grouped_profit = grouped_profit.resample('W-MON').agg({
                    'pure_profit': 'sum' 
                })
            elif period_profit == "Mensuel":
                grouped_profit = grouped_profit.resample('ME').agg({
                    'pure_profit': 'sum'
                })
            elif period_profit == "Annuel":
                grouped_profit = grouped_profit.resample('YE').agg({
                    'pure_profit': 'sum'
                })
            
            plot_df_profit = grouped_profit.reset_index()

            if period_profit in ["Hebdomadaire", "Mensuel", "Annuel"]:
                # Formater la date pour l'axe des X de l'histogramme
                if period_profit == "Hebdomadaire":
                    plot_df_profit['date_label'] = plot_df_profit['date'].dt.strftime('Sem. %U %Y')
                elif period_profit == "Mensuel":
                    plot_df_profit['date_label'] = plot_df_profit['date'].dt.strftime('%Y-%m')
                else: # Annuel
                    plot_df_profit['date_label'] = plot_df_profit['date'].dt.strftime('%Y')
                
                fig = px.bar(plot_df_profit, x='date_label', y='pure_profit',
                             title=f"Profit Net {period_profit}",
                             labels={'pure_profit': 'Profit Net (€)', 'date_label': 'Période'})
                fig.update_layout(yaxis=dict(zeroline=True, zerolinewidth=2, zerolinecolor='gray'))
                fig.update_xaxes(type='category', tickangle=45)
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"Profit Net {period_profit.lower()}.")
            else: # Journalier
                plot_df_profit['cumulative_pure_profit'] = plot_df_profit['pure_profit'].cumsum()
                fig = px.line(plot_df_profit, x='date', y='cumulative_pure_profit',
                              title="Profit Net Cumulé",
                              labels={'cumulative_pure_profit': 'Profit Net Cumulé (€)', 'date': 'Date'})
                fig.update_layout(yaxis=dict(zeroline=True, zerolinewidth=2, zerolinecolor='gray'))
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Profit net cumulé (évolution de la bankroll ajustée des dépôts/retraits).")
        
        with tab3:
            # Période pour les Tournois : Pas de Journalier
            period_tournaments = st.selectbox("Période des Tournois", ["Hebdomadaire", "Mensuel", "Annuel"], key=f"room_tournaments_period_{room_id}")

            # Recalculer les données groupées en fonction de period_tournaments
            grouped_tournaments = data.set_index('date')
            if period_tournaments == "Hebdomadaire":
                grouped_tournaments = grouped_tournaments.resample('W-MON').agg({
                    'tournaments': 'sum' 
                })
            elif period_tournaments == "Mensuel":
                grouped_tournaments = grouped_tournaments.resample('ME').agg({
                    'tournaments': 'sum'
                })
            elif period_tournaments == "Annuel":
                grouped_tournaments = grouped_tournaments.resample('YE').agg({
                    'tournaments': 'sum'
                })
            
            plot_df_tournaments = grouped_tournaments.reset_index()

            if period_tournaments == "Hebdomadaire":
                plot_df_tournaments['date_label_tournaments'] = plot_df_tournaments['date'].dt.strftime('Sem. %U %Y')
                x_axis_label = 'Période'
                x_data = plot_df_tournaments['date_label_tournaments']
            elif period_tournaments == "Mensuel":
                plot_df_tournaments['date_label_tournaments'] = plot_df_tournaments['date'].dt.strftime('%Y-%m')
                x_axis_label = 'Période'
                x_data = plot_df_tournaments['date_label_tournaments']
            else: # Annuel
                plot_df_tournaments['date_label_tournaments'] = plot_df_tournaments['date'].dt.strftime('%Y')
                x_axis_label = 'Période'
                x_data = plot_df_tournaments['date_label_tournaments']
            
            fig = px.bar(plot_df_tournaments, x=x_data, y='tournaments',
                        title=f"Nombre de tournois {period_tournaments}",
                        labels={'tournaments': 'Nombre de tournois', 'x': x_axis_label})
            
            fig.update_xaxes(type='category', tickangle=45) 
            
            st.plotly_chart(fig, use_container_width=True)

    else:
        st.warning("Aucune donnée disponible pour cette room sur la période sélectionnée.")

def global_stats():
    st.header("Profit Global")

    conn = get_db_connection()
    rooms = conn.execute("SELECT id, name, initial_bankroll, init_date FROM rooms").fetchall()
    
    if not rooms:
        st.warning("Configurez d'abord vos rooms")
        conn.close()
        return

    # Fetch all sessions data
    all_sessions_query = '''
    SELECT 
        room_id, 
        date, 
        bankroll, 
        cashflow, 
        tournaments
    FROM sessions
    ORDER BY room_id, date
    '''
    all_sessions_df = pd.read_sql(all_sessions_query, conn, parse_dates=['date'])
    conn.close()

    if all_sessions_df.empty and not rooms:
        st.warning("Aucune donnée disponible pour les sessions et les rooms.")
        return
    elif all_sessions_df.empty and rooms:
        # If no sessions, but rooms exist, we need to show initial bankroll for each room
        # This will be handled by the processing below, as we generate full_date_range from now()
        pass

    # Determine the overall date range (from earliest room init_date to today)
    min_date_room = min([pd.to_datetime(room[3]) for room in rooms]) if rooms else pd.Timestamp.now().floor('D')
    min_date_session = all_sessions_df['date'].min() if not all_sessions_df.empty else pd.Timestamp.now().floor('D')
    min_overall_date = min(min_date_room, min_date_session)
    
    max_overall_date = pd.Timestamp.now().floor('D') # Up to today

    # Generate a full date range for all relevant dates
    full_date_range = pd.date_range(start=min_overall_date, end=max_overall_date, freq='D')

    # Prepare an empty list to store processed dataframes for each room
    processed_room_dfs = []

    for room_id, room_name, initial_bankroll, init_date_str in rooms:
        room_init_date = pd.to_datetime(init_date_str)
        
        # Filter sessions for the current room
        original_room_sessions_df = all_sessions_df[all_sessions_df['room_id'] == room_id].copy()
        
        # Create a DataFrame with the full date range for this room, starting from its init_date
        room_specific_date_range = full_date_range[full_date_range >= room_init_date]
        
        if room_specific_date_range.empty:
            continue # Skip if no dates are relevant for this room

        # Reindex with the full date range to introduce missing dates
        # Mark original sessions to differentiate them from filled days
        room_sessions_df = original_room_sessions_df.set_index('date').reindex(room_specific_date_range)
        room_sessions_df['is_original_session'] = room_sessions_df['bankroll'].notna() # True if original, False if reindexed/missing
        
        # Fill NA for 'bankroll' with the previous valid bankroll.
        if room_sessions_df['bankroll'].iloc[0] is None or pd.isna(room_sessions_df['bankroll'].iloc[0]):
             room_sessions_df['bankroll'].iloc[0] = initial_bankroll
        
        room_sessions_df['bankroll'] = room_sessions_df['bankroll'].ffill()

        # Fill missing cashflow and tournaments with 0
        room_sessions_df['cashflow'] = room_sessions_df['cashflow'].fillna(0)
        room_sessions_df['tournaments'] = room_sessions_df['tournaments'].fillna(0)

        # Calculate shifted_bankroll: bankroll value from the previous day (or initial for first day)
        shifted_bankroll = room_sessions_df['bankroll'].shift(1)
        if room_sessions_df.index.min() == room_init_date:
            shifted_bankroll.loc[room_init_date] = initial_bankroll

        # Calculate pure_profit: (current_bankroll - current_cashflow) - previous_bankroll
        # This formula aligns with user's new clarification
        room_sessions_df['pure_profit'] = (room_sessions_df['bankroll'] - room_sessions_df['cashflow']) - shifted_bankroll
        
        # Calculate daily_profit (change in bankroll from previous day)
        # This calculation is distinct from pure_profit and seems correct as is for "daily bankroll change"
        room_sessions_df['daily_profit'] = room_sessions_df['bankroll'] - shifted_bankroll.fillna(0)


        # Add room_id and room_name back
        room_sessions_df['room_id'] = room_id
        room_sessions_df['room_name'] = room_name
        
        processed_room_dfs.append(room_sessions_df.reset_index().rename(columns={'index': 'date'}))

    if not processed_room_dfs:
        st.warning("Aucune donnée de session traitée pour les rooms configurées.")
        return

    df_global = pd.concat(processed_room_dfs)
    
    # Group by date and sum across all rooms for global totals
    data = df_global.groupby('date').agg(
        total_bankroll=('bankroll', 'sum'),
        pure_profit=('pure_profit', 'sum'), # pure_profit already calculated per room, now sum globally
        total_tournaments=('tournaments', 'sum')
    ).reset_index() # Important: reset index here so 'date' is a column for plotly

    if not data.empty:
        # Calcul des métriques
        total_initial = sum(room[2] for room in rooms) # Sum of initial bankrolls across all rooms
        current_bankroll = data['total_bankroll'].iloc[-1] if not data.empty else 0
        pure_profit_total_period = data['pure_profit'].sum() # Summing up all pure_profits from the grouped data
        total_tournaments = int(data['total_tournaments'].sum()) if not data.empty else 0

        # Affichage des KPI
        st.subheader("Synthèse Globale")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("💰 Bankroll actuelle", f"{current_bankroll:.2f}€")
        with col2:
            profit_pct = (pure_profit_total_period / total_initial * 100) if total_initial > 0 else 0
            st.metric("📈 Profit Net", f"{pure_profit_total_period:.2f}€", delta=f"{profit_pct:.1f}%")
        with col3: 
            st.metric("🏆 Tournois", f"{total_tournaments}")

        tab1, tab2, tab3 = st.tabs(["Bankroll Cumulée", "Profit net", "Tournois"])
        
        with tab1:
            # Période pour la Bankroll Cumulée : Uniquement Journalier
            period_br = st.selectbox("Période de la Bankroll", ["Journalier"], key="global_br_period")
            
            # Le graphique de bankroll cumulée utilise toujours les données journalières
            fig = px.line(data, x='date', y='total_bankroll',
                          title="Évolution de la bankroll cumulée",
                          labels={'total_bankroll': 'Bankroll Cumulée (€)', 'date': 'Date'})
            st.plotly_chart(fig, use_container_width=True)
        
        with tab2: # Profit net
            # Période pour le Profit : Toutes les options
            period_profit = st.selectbox("Période du Profit", ["Journalier", "Hebdomadaire", "Mensuel", "Annuel"], key="global_profit_period")
            
            # Recalculer les données groupées en fonction de period_profit
            grouped_profit = data.set_index('date')
            if period_profit == "Hebdomadaire":
                grouped_profit = grouped_profit.resample('W-MON').agg({
                    'pure_profit': 'sum' 
                })
            elif period_profit == "Mensuel":
                grouped_profit = grouped_profit.resample('ME').agg({
                    'pure_profit': 'sum'
                })
            elif period_profit == "Annuel":
                grouped_profit = grouped_profit.resample('YE').agg({
                    'pure_profit': 'sum'
                })
            
            plot_df_profit = grouped_profit.reset_index()

            if period_profit in ["Hebdomadaire", "Mensuel", "Annuel"]:
                if period_profit == "Hebdomadaire":
                    plot_df_profit['date_label'] = plot_df_profit['date'].dt.strftime('Sem. %U %Y')
                elif period_profit == "Mensuel":
                    plot_df_profit['date_label'] = plot_df_profit['date'].dt.strftime('%Y-%m')
                else: # Annuel
                    plot_df_profit['date_label'] = plot_df_profit['date'].dt.strftime('%Y')
                
                fig = px.bar(plot_df_profit, x='date_label', y='pure_profit',
                             title=f"Profit Net {period_profit}",
                             labels={'pure_profit': 'Profit Net (€)', 'date_label': 'Période'})
                fig.update_layout(yaxis=dict(zeroline=True, zerolinewidth=2, zerolinecolor='gray'))
                fig.update_xaxes(type='category', tickangle=45) 

                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"Profit net {period_profit}")
            else: # Journalier
                plot_df_profit['cumulative_pure_profit'] = plot_df_profit['pure_profit'].cumsum()
                fig = px.line(plot_df_profit, x='date', y='cumulative_pure_profit',
                              title="Profit Net Cumulé",
                              labels={'cumulative_pure_profit': 'Profit Net Cumulé (€)', 'date': 'Date'})
                fig.update_layout(yaxis=dict(zeroline=True, zerolinewidth=2, zerolinecolor='gray'))
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Profit net cumulé")
        
        with tab3: # Tournois globaux
            # Période pour les Tournois : Pas de Journalier
            period_tournaments = st.selectbox("Période des Tournois", ["Hebdomadaire", "Mensuel", "Annuel"], key="global_tournaments_period")

            # Recalculer les données groupées en fonction de period_tournaments
            grouped_tournaments = data.set_index('date')
            if period_tournaments == "Hebdomadaire":
                grouped_tournaments = grouped_tournaments.resample('W-MON').agg({
                    'total_tournaments': 'sum' 
                })
            elif period_tournaments == "Mensuel":
                grouped_tournaments = grouped_tournaments.resample('ME').agg({
                    'total_tournaments': 'sum'
                })
            elif period_tournaments == "Annuel":
                grouped_tournaments = grouped_tournaments.resample('YE').agg({
                    'total_tournaments': 'sum'
                })
            
            plot_df_tournaments = grouped_tournaments.reset_index()

            if period_tournaments == "Hebdomadaire":
                plot_df_tournaments['date_label_tournaments'] = plot_df_tournaments['date'].dt.strftime('Sem. %U %Y')
                x_axis_label = 'Période'
                x_data = plot_df_tournaments['date_label_tournaments']
            elif period_tournaments == "Mensuel":
                plot_df_tournaments['date_label_tournaments'] = plot_df_tournaments['date'].dt.strftime('%Y-%m')
                x_axis_label = 'Période'
                x_data = plot_df_tournaments['date_label_tournaments']
            else: # Annuel
                plot_df_tournaments['date_label_tournaments'] = plot_df_tournaments['date'].dt.strftime('%Y')
                x_axis_label = 'Période'
                x_data = plot_df_tournaments['date_label_tournaments']
            
            fig = px.bar(plot_df_tournaments, x=x_data, y='total_tournaments',
                        title=f"Nombre total de tournois {period_tournaments}",
                        labels={'total_tournaments': 'Nombre de tournois', 'x': x_axis_label})
            fig.update_xaxes(type='category', tickangle=45)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"Nombre total de tournois joués par {period_tournaments.lower()}.")
    else:
        st.warning("Aucune donnée disponible")

def delete_room():
    st.header("❌ Supprimer une Room")

    conn = get_db_connection()
    rooms = conn.execute("SELECT id, name FROM rooms").fetchall()
    conn.close()

    if not rooms:
        st.warning("Aucune room configurée.")
        return

    room_choice = st.selectbox("Room à supprimer", [r[1] for r in rooms])
    room_id = next(r[0] for r in rooms if r[1] == room_choice)

    if st.button("Supprimer la Room et ses sessions"):
        conn = get_db_connection()

        # Historique de la room
        old_room_data = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        conn.execute('''
        INSERT INTO edits_history (table_name, record_id, old_value, new_value, edit_time, user_action)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        ''', (
            'rooms',
            room_id,
            str(old_room_data),
            'DELETED',
            'DELETE'
        ))

        # Historique des sessions de cette room
        session_data = conn.execute("SELECT * FROM sessions WHERE room_id = ?", (room_id,)).fetchall()
        for session in session_data:
            conn.execute('''
            INSERT INTO edits_history (table_name, record_id, old_value, new_value, edit_time, user_action)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ''', (
                'sessions',
                session[0],
                str(session),
                'DELETED',
                'DELETE_CASCADE'
            ))

        # Suppression des données
        conn.execute("DELETE FROM sessions WHERE room_id = ?", (room_id,))
        conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        conn.commit()
        conn.close()

        st.success(f"La room '{room_choice}' et toutes ses sessions ont été supprimées.")
        st.rerun()

def session_history():
    st.header("Historique des Sessions")

    conn = get_db_connection()
    rooms = conn.execute("SELECT id, name FROM rooms").fetchall()

    room_filter = st.selectbox("Filtrer par room", ["Toutes"] + [r[1] for r in rooms])

    query = '''
    SELECT 
        s.id, 
        r.name as room, 
        s.date, 
        s.tournaments, 
        s.cashflow,
        s.bankroll,
        (s.bankroll - LAG(s.bankroll, 1, r.initial_bankroll) OVER (PARTITION BY s.room_id ORDER BY s.date)) as session_profit,
        ((s.bankroll - s.cashflow) - LAG(s.bankroll - s.cashflow, 1, r.initial_bankroll) OVER (PARTITION BY s.room_id ORDER BY s.date)) as pure_profit
    FROM sessions s
    JOIN rooms r ON s.room_id = r.id
    '''

    if room_filter != "Toutes":
        query += f" WHERE r.name = '{room_filter}'"

    query += " ORDER BY s.date DESC"

    sessions = pd.read_sql(query, conn)
    
    # Affichage de l'historique
    st.dataframe(sessions, height=400)
    
    # Gestion des modifications
    st.subheader("Modifier une Session")
    session_id = st.number_input("ID de la session à modifier", min_value=1)

    # Utilisation de st.session_state pour stocker les données de la session à modifier
    if 'session_to_edit' not in st.session_state:
        st.session_state.session_to_edit = None

    if st.button("Charger la session"):
        session_data = conn.execute('''
        SELECT s.id, s.date, r.name, s.tournaments, s.cashflow, s.bankroll
        FROM sessions s
        JOIN rooms r ON s.room_id = r.id
        WHERE s.id = ?
        ''', (session_id,)).fetchone()

        if session_data:
            st.session_state.session_to_edit = session_data # Stocke les données dans session_state
        else:
            st.warning("Session introuvable. Veuillez vérifier l'ID.")
            st.session_state.session_to_edit = None # Réinitialise si non trouvée

    # Affiche le formulaire de modification si une session est chargée
    if st.session_state.session_to_edit:
        session_data = st.session_state.session_to_edit
        
        # Assurez-vous que la date est un objet date pour st.date_input
        current_date_obj = datetime.strptime(session_data[1], "%Y-%m-%d").date()
        
        # Utilisez des clés uniques pour les inputs Streamlit
        new_date = st.date_input("Date", value=current_date_obj, key="edit_date")
        new_tournaments = st.number_input("Tournois", value=session_data[3], key="edit_tournaments")
        new_cashflow = st.number_input("Cashflow", value=session_data[4], key="edit_cashflow")
        new_bankroll = st.number_input("Bankroll", value=session_data[5], key="edit_bankroll")

        # NOUVEAU: Utilisation d'un simple st.button au lieu de st.form_submit_button
        if st.button("Enregistrer les modifications", key="save_edits_button"):
            try:
                # Sauvegarde avant modification
                old_values_str = str(session_data[1:])
                new_values_str = str((new_date, new_tournaments, new_cashflow, new_bankroll))

                conn.execute('''
                INSERT INTO edits_history (table_name, record_id, old_value, new_value, edit_time, user_action)
                VALUES (?, ?, ?, ?, datetime('now'), ?)
                ''', (
                    'sessions',
                    session_id, # Utilisez l'ID d'origine chargé, pas le nouveau session_id du number_input principal
                    old_values_str,
                    new_values_str,
                    'UPDATE'
                ))

                # Mise à jour
                conn.execute('''
                UPDATE sessions
                SET date = ?, tournaments = ?, cashflow = ?, bankroll = ?
                WHERE id = ?
                ''', (str(new_date), new_tournaments, new_cashflow, new_bankroll, session_id)) # Utilisez l'ID d'origine
                
                conn.commit()

                st.success("Session mise à jour avec succès !")
                # Optionnel: effacer la session_to_edit pour cacher le formulaire après la MAJ
                st.session_state.session_to_edit = None 
                st.rerun()
            except Exception as e:
                conn.rollback()
                st.error(f"Erreur lors de l'enregistrement de la session: {e}")
                print(f"ERREUR DANS SESSION UPDATE (catch block): {e}")

    # Suppression de session
    st.subheader("Supprimer une Session")
    session_id_to_delete = st.number_input("ID de la session à supprimer", min_value=1, key="delete_session_id")

    if st.button("Supprimer la session"):
        old_session = conn.execute('''
        SELECT s.date, r.name, s.tournaments, s.cashflow, s.bankroll
        FROM sessions s
        JOIN rooms r ON s.room_id = r.id
        WHERE s.id = ?
        ''', (session_id_to_delete,)).fetchone()

        if old_session:
            conn.execute('''
            INSERT INTO edits_history (table_name, record_id, old_value, new_value, edit_time, user_action)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ''', (
                'sessions',
                session_id_to_delete,
                str(old_session),
                'DELETED',
                'DELETE'
            ))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id_to_delete,))
            conn.commit()
            st.success("Session supprimée avec succès !")
            st.rerun()
        else:
            st.warning("Session introuvable")
    
    conn.close()

def dashboard():
    st.header("📊 Tableau de Bord Global")
    
    conn = get_db_connection()
    
    # Requête simplifiée sans cashflows séparés
    query = '''
    WITH last_sessions AS (
        SELECT 
            room_id, 
            bankroll,
            cashflow,
            tournaments,
            ROW_NUMBER() OVER (PARTITION BY room_id ORDER BY date DESC) as rn
        FROM sessions
    ),
    room_stats AS (
        SELECT 
            room_id,
            COUNT(id) as total_sessions,
            SUM(tournaments) as total_tournaments,
            SUM(cashflow) as total_cashflow
        FROM sessions
        GROUP BY room_id
    )
    SELECT 
        r.name, 
        r.initial_bankroll, 
        r.init_date,
        COALESCE(ls.bankroll, r.initial_bankroll) as current_bankroll,
        COALESCE(ls.cashflow, 0) as last_cashflow,
        COALESCE(rs.total_sessions, 0) as total_sessions,
        COALESCE(rs.total_tournaments, 0) as total_tournaments,
        COALESCE(ls.bankroll, r.initial_bankroll) - r.initial_bankroll as gross_profit,
        (COALESCE(ls.bankroll, r.initial_bankroll) - COALESCE(rs.total_cashflow, 0)) - r.initial_bankroll as net_profit
    FROM rooms r
    LEFT JOIN last_sessions ls ON r.id = ls.room_id AND ls.rn = 1
    LEFT JOIN room_stats rs ON r.id = rs.room_id
    ORDER BY r.name
    '''
    
    rooms_data = conn.execute(query).fetchall()
    
    if not rooms_data:
        st.warning("Configurez d'abord vos rooms")
        conn.close()
        return
    
    df_rooms = pd.DataFrame(rooms_data, columns=[
        'Room', 'BR Initiale', 'Date Init', 'BR Actuelle', 
        'Last Cashflow', 'Sessions', 'Tournois', 
        'Profit Brut', 'Profit Net'
    ])
    
    # Calcul des totaux
    total_initial = df_rooms['BR Initiale'].sum()
    total_current = df_rooms['BR Actuelle'].sum()
    total_net_profit = df_rooms['Profit Net'].sum()
    total_tournaments = df_rooms['Tournois'].sum()
    
    # Affichage des KPI
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("💰 Bankroll actuelle", f"{total_current:.2f}€")
    with col2:
        profit_pct = (total_net_profit / total_initial * 100) if total_initial > 0 else 0
        st.metric("📈 Profit Net", f"{total_net_profit:.2f}€", delta=f"{profit_pct:.1f}%")
    with col3: 
        st.metric("🏆 Tournois", f"{total_tournaments}")
    
    # Détail par room
    st.subheader("📋 Détail par Room")
    df_rooms['ROI %'] = ((df_rooms['Profit Net'] / df_rooms['BR Initiale']) * 100).round(1)
    
    # Formatage
    display_cols = ['Room', 'BR Initiale', 'BR Actuelle', 'Sessions', 
                   'Tournois', 'Profit Brut', 'Profit Net', 'ROI %']
    st.dataframe(df_rooms[display_cols], use_container_width=True)
    
    # Graphiques
    st.subheader("📊 Répartition")
    tab1, tab2 = st.tabs(["Bankroll", "Profit net"])
    
    with tab1:
        fig = px.pie(df_rooms, names='Room', values='BR Actuelle', 
                     title="Répartition de la Bankroll")
        st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        fig = px.bar(df_rooms, x='Room', y='Profit Net',
                     title="Profit Net par Room",
                     color='Profit Net',
                     color_continuous_scale=['red', 'green'])
        fig.update_layout(yaxis=dict(zeroline=True, zerolinewidth=2, zerolinecolor='gray'))
        st.plotly_chart(fig, use_container_width=True)
    
    conn.close()


def edit_initial_bankroll():
    st.header("Modifier Bankroll Initiale")

    conn = get_db_connection()
    rooms = conn.execute("SELECT id, name, initial_bankroll, init_date FROM rooms").fetchall()
    conn.close()

    if not rooms:
        st.warning("Configurez d'abord vos rooms")
        return

    room_choice = st.selectbox("Room à modifier", [r[1] for r in rooms])
    room_data = next(r for r in rooms if r[1] == room_choice)
    room_id, room_name, current_br, current_date = room_data

    col1, col2 = st.columns(2)
    with col1:
        new_br = st.number_input("Nouvelle bankroll initiale", value=current_br)
    with col2:
        new_date = st.date_input("Nouvelle date d'initialisation", 
                                value=datetime.strptime(current_date, "%Y-%m-%d").date())

    if st.button("Enregistrer"):
        conn = get_db_connection()
        # Historique
        conn.execute('''
        INSERT INTO edits_history (table_name, record_id, old_value, new_value, edit_time, user_action)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        ''', (
            'rooms',
            room_id,
            str((current_br, current_date)),
            str((new_br, str(new_date))),
            'UPDATE_INITIAL_BR_DATE'
        ))

        # Mise à jour
        conn.execute('''
        UPDATE rooms
        SET initial_bankroll = ?, init_date = ?
        WHERE id = ?
        ''', (new_br, str(new_date), room_id))
        conn.commit()
        conn.close()

        st.success(f"Bankroll initiale et date de {room_choice} mises à jour!")
        st.rerun()
            
def main():
    st.title("Poker Bankroll Tracker")

    menu = [
        "🏠 Tableau de Bord",
        "📊 Stats par Room",
        "🌍 Vue globale",
        "⚙️ Initialisation",
        "➕ Nouvelle Session",
        "📝 Historique/Modifications",
        "💰 Bankroll Initiale",
        "❌ Supprimer une Room"
    ]
    choice = st.sidebar.selectbox("Menu", menu)

    if choice == "🏠 Tableau de Bord":
        dashboard()
    elif choice == "📊 Stats par Room":
        room_stats()
    elif choice == "🌍 Vue globale":
        global_stats()
    elif choice == "⚙️ Initialisation":
        setup_rooms()
    elif choice == "➕ Nouvelle Session":
        add_session()
    elif choice == "📝 Historique/Modifications":
        session_history()
    elif choice == "💰 Bankroll Initiale":
        edit_initial_bankroll()
    elif choice == "❌ Supprimer une Room":
        delete_room()

if __name__ == "__main__":
    main()
import streamlit as st
import pandas as pd
from texts.request import TextsRequest
from dictionary.vars import PLATFORMS
from services.embeddings_service import EmbeddingsService
from services.redis_service import RedisService
from services.text_generation_service import TextGenerationService
import logging

logger = logging.getLogger(__name__)


class Texts:
    """
    Classe que representa os métodos referentes à geração de post natural.
    Implementa o fluxo completo conforme nova arquitetura:
    - Busca de embeddings via API externa
    - Armazenamento no Redis
    - Interface para input do usuário
    - Elaboração de prompt com referências
    - Geração de post via LLM
    """

    def __init__(self):
        self.embeddings_service = EmbeddingsService()
        self.redis_service = RedisService()
        self.text_service = TextGenerationService()

    def treat_texts_dataframe(self, texts_data):
        """
        Realiza o tratamento e formatação dos dados referentes aos posts.

        Parameters
        ----------
        texts_data : list
            A série de dados referentes aos posts gerados.

        Returns
        -------
        df : DataFrame
            A série de dados tratados.
        """

        df = pd.DataFrame(texts_data)
        df = df.drop(columns=['id'])

        # Mapear campos da API para campos esperados pelo frontend
        if 'content' in df.columns and 'generated_text' not in df.columns:
            df['generated_text'] = df['content']

        # Mapear status baseado no campo is_approved da API
        if 'is_approved' in df.columns and 'status' not in df.columns:
            df['status'] = df['is_approved'].apply(
                lambda x: 'approved' if x else 'pending_approval')

        df = df.rename(
            columns={
                'theme': 'Tema',
                'generated_text': 'Post Gerado',
                'created_at': 'Data de Criação',
                'status': 'Status',
            }
        )

        df = df[[
            "Tema",
            "Post Gerado",
            "Data de Criação",
            "Status",
        ]
        ]

        df = df.sort_values(by="Data de Criação", ascending=False)

        return df

    def get_texts_index(self, texts):
        """
        Obtém o índice dos posts, com seus temas e identificadores.

        Parameters
        ----------
        texts : dict
            Dicionário com os dados dos posts.

        Returns
        -------
        texts_index : dict
            Dicionário com os índices.
        """
        texts_topics = []
        texts_ids = []

        for text in texts:
            texts_topics.append(text['theme'])
            texts_ids.append(text['id'])

        texts_index = dict(zip(texts_topics, texts_ids))

        return texts_index

    def validate_topic(self, topic):
        """
        Valida o tema informado (versão simplificada sem st.error visual).

        Parameters
        ----------
        topic : str
            O tema para geração de post.

        Returns
        -------
        bool
            se o tema informado é ou não válido.
        str
            O tema validado.
        """
        if not topic or len(topic.strip()) < 5:
            return False, topic

        if len(topic.strip()) > 500:
            return False, topic

        return True, topic.strip()

    def _process_text_generation_improved(
            self,
            user_topic: str,
            search_query: str,
            platform: str,
            tone: str,
            creativity_level: str,
            length: str,
            include_hashtags: bool,
            include_cta: bool,
            token: str,
            result_container):
        """
        Processa a geração completa de post seguindo o fluxo do roadmap.
        """
        # Criar interface de progresso na área de resultado
        with result_container.container():
            st.markdown("""
            <div style="text-align: center; margin-bottom: 20px;">
                <h3 style="color: #1f77b4;">🚀 Gerando Post com IA</h3>
                <p style="color: #666;">
                    Processando sua solicitação...
                </p>
            </div>
            """, unsafe_allow_html=True)

            progress_bar = st.progress(0)
            status_text = st.empty()

        try:
            # 1. Verificar cache Redis
            status_text.text("🔍 Verificando cache Redis...")
            progress_bar.progress(10)

            cached_embeddings = self.redis_service.get_cached_embeddings(
                search_query
            )

            if cached_embeddings:
                similar_texts = cached_embeddings.get('similar_texts', [])
                status_text.text("✅ Embeddings encontrados no cache")
            else:
                # 2. Busca via API de embeddings
                status_text.text("🔍 Buscando embeddings via API...")
                progress_bar.progress(30)

                raw_texts = self.embeddings_service.search_texts(search_query)

                # Permite geração mesmo sem resultados da API
                if raw_texts:
                    # 3. Tratamento dos textos da API
                    status_text.text("⚙️ Tratando textos encontrados...")
                    progress_bar.progress(50)

                    texts = self.text_service.treat_text_content(raw_texts)

                    if texts:
                        # 4. Busca de textos similares via API
                        status_text.text("🎯 Encontrando textos similares...")
                        progress_bar.progress(70)

                        similar_texts = (
                            self.text_service.find_similar_texts_via_api(
                                user_topic,
                                texts
                            )
                        )

                        # 5. Cache no Redis
                        if similar_texts:
                            self.redis_service.cache_embeddings(
                                search_query, {'similar_texts': similar_texts})

                            # Mostrar preview dos embeddings encontrados
                            status_text.markdown(f"""
                            ✅ **{len(similar_texts)} embeddings encontrados!**

                            **Top 3 referências mais relevantes:**
                            """)

                            for i, (
                                text,
                                score
                            ) in enumerate(similar_texts[:3], 1):
                                title = text.get('title', 'Sem título')
                                text_type = text.get('type', 'Conteúdo')
                                score_percentage = round(score * 100, 1)

                                status_text.markdown(f"""
                                **{i}.** {title} *(Relevância: {
                                    score_percentage
                                }%)*
                                📂 {text_type}
                                """)

                            # Pequena pausa para permitir visualização
                            import time
                            time.sleep(2)
                        else:
                            st.toast(
                                "Nenhuma referência similar encontrada",
                                icon="ℹ️"
                            )
                            similar_texts = []
                    else:
                        st.toast(
                            """Textos inválidos após tratamento,
                            prosseguindo sem referências""",
                            icon="⚠️")
                        similar_texts = []
                else:
                    st.toast(
                        """Nenhuma referência encontrada na API,
                        gerando baseado apenas no tema""",
                        icon="ℹ️")
                    similar_texts = []

            # 6. Elaboração do contexto de prompt com novos parâmetros
            status_text.text("📝 Criando contexto do prompt...")
            progress_bar.progress(80)

            prompt_context = self.text_service.create_prompt_context(
                user_topic,
                similar_texts,
                platform,
                tone,
                creativity_level,
                length
            )

            # 7. Geração de post via OpenAI/LLM
            status_text.text("🤖 Gerando post com IA...")
            progress_bar.progress(90)

            generated_text = self.text_service.generate_text_via_llm(
                prompt_context
            )
            if not generated_text:
                st.toast("Erro na geração de post via IA", icon="❌")
                return

            # 8. Envio para aprovação via webhook
            status_text.text("📤 Enviando para aprovação...")
            progress_bar.progress(95)

            approval_sent = self.text_service.send_for_approval(
                generated_text, user_topic)

            # 9. Salvar no banco de dados da API UniPost
            text_data = {
                "theme": user_topic,
                "platform": platform if platform else "GENERIC",
                "content": generated_text,
                "is_approved": False  # Sempre False inicialmente
            }

            # Registrar na API do projeto unipost-api
            try:
                send_result = TextsRequest().create_text(
                    token=token,
                    text_data=text_data
                )
                logger.info(
                    f"Text successfully registered in API: {send_result}")
            except Exception as api_error:
                logger.error(f"Error registering in API: {api_error}")
                send_result = "Erro ao registrar na API"

            progress_bar.progress(100)
            status_text.text("✅ Processo concluído com sucesso!")

            # Processamento concluído

            # Limpar barra de progresso antes de mostrar resultado
            progress_bar.empty()
            status_text.empty()

            # Exibir resultado na área direita
            with result_container.container():
                st.toast("Post Gerado com Sucesso!", icon="✅")

                # Informações dos parâmetros
                platform_name = (PLATFORMS.get(platform, 'Genérico')
                                 if platform else 'Genérico')

                # Contar palavras do post gerado
                word_count = len(
                    generated_text.split()
                ) if generated_text else 0
                target_count = self.text_service.extract_word_count(length)

                # Cor baseada na precisão da contagem
                count_color = "#28a745" if abs(
                    word_count - target_count
                ) <= 20 else "#ffc107"

                st.markdown(f"""
                <div style="
                    background: #e7f3ff;
                    padding: 10px;
                    border-radius: 6px;
                    margin-bottom: 15px;
                    font-size: 12px;
                    border-left: 3px solid #007bff;
                ">
                    📱 {platform_name} • 📝 {tone.title()} •
                    🎨 {creativity_level.title()} • 📏 {length}
                    <br>📚 {len(similar_texts)} referências •
                    <span style="color: {count_color}; font-weight: bold;">
                        📊 {word_count} palavras (alvo: {target_count})
                    </span>
                </div>
                """, unsafe_allow_html=True)

                # Post gerado principal
                formatted_text = generated_text.replace('\n', '<br>')
                st.markdown(f"""
                <div style="
                    background: white;
                    padding: 20px;
                    border-radius: 10px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    border: 1px solid #e9ecef;
                    margin-bottom: 15px;
                    line-height: 1.6;
                    font-size: 15px;
                ">
                {formatted_text}
                </div>
                """, unsafe_allow_html=True)

                # Botões de ação (mesmo padrão da lista)
                st.markdown("**🎛️ Ações:**")
                col_approve, col_reject, col_regenerate = st.columns(3)

                # Botão Aprovar/Aprovado
                with col_approve:
                    approve_disabled = approval_sent
                    if st.button(
                        "✅ Aprovar" if not approval_sent else "✅ Aprovado",
                        key="approve_generated",
                        use_container_width=True,
                        type="secondary" if not approve_disabled else (
                            "primary"
                        ),
                        disabled=approve_disabled,
                        help="Marcar como aprovado" if not approve_disabled
                        else "Já foi para aprovação"
                    ):
                        # Simular aprovação (post já foi registrado na API)
                        st.toast("Status atualizado para aprovado!", icon="✅")
                        if 'last_generated' in st.session_state:
                            st.session_state[
                                'last_generated'
                            ]['approved'] = True
                        st.rerun()

                # Botão Reprovar/Reprovado
                with col_reject:
                    reject_disabled = not approval_sent
                    if st.button(
                        "❌ Reprovar" if approval_sent else "❌ Reprovado",
                        key="reject_generated",
                        use_container_width=True,
                        type="secondary" if not reject_disabled else "primary",
                        disabled=reject_disabled,
                        help="Marcar como reprovado" if not reject_disabled
                        else "Ainda não foi aprovado"
                    ):
                        st.toast("Status atualizado para reprovado!", icon="❌")
                        if 'last_generated' in st.session_state:
                            st.session_state[
                                'last_generated'
                            ]['approved'] = False
                        st.rerun()

                # Botão Regenerar (sempre ativo)
                with col_regenerate:
                    if st.button(
                        "🔄 Regenerar",
                        key="regenerate_generated",
                        use_container_width=True,
                        type="secondary",
                        help="Gerar novo post com os mesmos parâmetros"
                    ):
                        # Preparar dados para regeneração
                        if 'last_generated' in st.session_state:
                            last_data = st.session_state['last_generated']
                            st.session_state.regenerate_text_data = {
                                'theme': last_data.get('theme', ''),
                                'platform': platform,
                                'tone': tone,
                                'creativity': creativity_level,
                                'length': length
                            }
                            del st.session_state['last_generated']

                        st.toast(
                            "Regenerando post com os mesmos parâmetros...",
                            icon="🔄"
                        )
                        st.rerun()

                # Referências detalhadas com embeddings encontrados
                if similar_texts:
                    with st.expander(f"""📖 Embeddings Encontrados ({
                        len(similar_texts)
                        } referências)""", expanded=True
                    ):
                        st.markdown("### 🔍 Referências utilizadas na geração:")

                        for i, (
                            text,
                            score
                        ) in enumerate(similar_texts[:5], 1):
                            # Informações do embedding
                            title = text.get('title', 'Sem título')
                            text_type = text.get('type', 'Conteúdo Geral')
                            index_source = text.get('index', 'unknown')
                            text_content = text.get('text', '')[:300]

                            # Score em porcentagem para melhor visualização
                            score_percentage = round(score * 100, 1)

                            # Determinar cor baseada no score
                            if score >= 0.7:
                                score_color = "#28a745"
                                relevance_text = "Alta"
                            elif score >= 0.4:
                                score_color = "#ffc107"
                                relevance_text = "Média"
                            else:
                                score_color = "#6c757d"
                                relevance_text = "Baixa"

                            st.markdown(f"""
                            <div style="
                                background: #f8f9fa;
                                border-left: 4px solid {score_color};
                                padding: 15px;
                                margin: 10px 0;
                                border-radius: 5px;
                                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                            ">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                                    <h4 style="margin: 0; color: #333; font-size: 16px;">
                                        📄 {title}
                                    </h4>
                                    <div style="
                                        background: {score_color};
                                        color: white;
                                        padding: 4px 8px;
                                        border-radius: 12px;
                                        font-size: 12px;
                                        font-weight: bold;
                                    ">
                                        {relevance_text}: {score_percentage}%
                                    </div>
                                </div>

                                <div style="margin-bottom: 8px;">
                                    <span style="
                                        background: #e9ecef;
                                        padding: 2px 6px;
                                        border-radius: 8px;
                                        font-size: 11px;
                                        color: #495057;
                                        margin-right: 8px;
                                    ">
                                        🏷️ {text_type}
                                    </span>
                                    <span style="
                                        background: #dee2e6;
                                        padding: 2px 6px;
                                        border-radius: 8px;
                                        font-size: 11px;
                                        color: #495057;
                                    ">
                                        🗂️ {index_source}
                                    </span>
                                </div>

                                <div style="
                                    color: #666;
                                    font-size: 13px;
                                    line-height: 1.4;
                                    background: white;
                                    padding: 10px;
                                    border-radius: 4px;
                                    border: 1px solid #e9ecef;
                                ">
                                    <strong>Conteúdo utilizado como referência:
                                    </strong><br>
                                    {text_content}{'...' if len(
                                        text.get('text', '')) > 300 else ''}
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                        if len(similar_texts) > 5:
                            st.info(
                                f"""Mostrando 5 de {
                                    len(similar_texts)
                                } referências encontradas.
                                As referências com maior score de
                                similaridade foram priorizadas."""
                            )

                        # Resumo estatístico dos embeddings
                        st.markdown("---")
                        st.markdown("### 📊 Resumo das Referências:")

                        (
                            col_stats1, col_stats2, col_stats3, col_stats4
                        ) = st.columns(4)

                        with col_stats1:
                            st.metric(
                                "Total de Referências", len(similar_texts)
                            )

                        with col_stats2:
                            avg_score = sum(
                                score for _, score in similar_texts
                            ) / len(similar_texts)
                            st.metric("Score Médio", f"{avg_score:.2f}")

                        with col_stats3:
                            high_relevance = sum(
                                1 for _, score in similar_texts if (
                                    score >= 0.7
                                ))
                            st.metric("Alta Relevância", high_relevance)

                        with col_stats4:
                            # Contar tipos únicos de referências
                            unique_types = set(
                                text.get(
                                    'type',
                                    'Geral'
                                ) for text, _ in similar_texts)
                            st.metric("Tipos de Fonte", len(unique_types))

                        # Mostrar tipos de fontes encontradas
                        if len(unique_types) > 1:
                            st.markdown("**🗂️ Tipos de fontes consultadas:**")
                            types_text = ", ".join(sorted(unique_types))
                            st.caption(types_text)
                else:
                    # Exibir aviso quando não há referências
                    with st.expander(
                        "📖 Embeddings Encontrados (0 referências)",
                        expanded=True
                    ):
                        st.warning("""
                        🔍 **Nenhuma referência encontrada**

                        Este post foi gerado baseado apenas no tema fornecido,
                        sem utilizar referências do banco de dados
                        de embeddings.

                        **Possíveis motivos:**
                        - Tema muito específico ou novo
                        - Base de dados ainda não contém conteúdo relacionado
                        - Termos de busca não encontraram correspondências

                        **Dica:** Tente reformular o tema ou usar termos
                        mais gerais para encontrar referências relacionadas.
                        """)

                        # Estatísticas quando não há referências
                        (
                            col_empty1, col_empty2, col_empty3, col_empty4
                        ) = st.columns(4)

                        with col_empty1:
                            st.metric("Total de Referências", 0)
                        with col_empty2:
                            st.metric("Score Médio", "N/A")
                        with col_empty3:
                            st.metric("Alta Relevância", 0)
                        with col_empty4:
                            st.metric("Tipos de Fonte", 0)

                st.toast(f"✅ {send_result}")

                # Salvar na sessão com ID do post criado
                st.session_state['last_generated'] = {
                    'text': generated_text,
                    'platform': platform_name,
                    'tone': tone,
                    'creativity': creativity_level,
                    'length': length,
                    'text_data': text_data,  # Dados enviados para API
                    'approved': approval_sent,
                    'theme': user_topic
                }

        except Exception as e:
            st.toast(f"Erro durante o processamento: {e}", icon="❌")
            logger.error(f"Error in text generation process: {e}")

        finally:
            # Garantir limpeza dos elementos de progresso
            try:
                progress_bar.empty()
                status_text.empty()
            except Exception:
                pass  # Elementos podem já ter sido removidos

    def create(self, token, menu_position, permissions):
        """
        Gera um novo post usando IA.

        Parameters
        ----------
        token : str
            O token obtido e passado para a validação da requisição.
        menu_position : Any
            A posição do menu superior.
        permissions : list
            A lista de permissões do usuário.
        """
        # Exibir status dos serviços no menu
        if 'create' in permissions:

            # Layout principal: Parâmetros | Resultado
            # Novos campos obrigatórios
            col_params, col_result = st.columns([1.0, 1.2])

            with col_params:
                st.subheader("🎨 Parâmetros de Geração")

                # Verificar se há dados de regeneração salvos
                regenerate_data = st.session_state.get(
                    'regenerate_text_data',
                    {}
                )
                default_theme = regenerate_data.get('theme', '')

                # Mostrar aviso se for regeneração
                if regenerate_data:
                    st.info(
                        "🔄 **Modo Regeneração**: Tema carregado " +
                        "automaticamente do post selecionado."
                    )

                text_topic = st.text_area(
                    label="🎯 Tema do post",
                    value=default_theme,
                    max_chars=500,
                    placeholder="Ex: Benefícios da energia renovável" +
                    " para o meio ambiente",
                    help="Descreva detalhadamente o tema que deseja abordar.",
                    height=120,
                    key="topic_input")

                search_query = st.text_input(
                    label="🔍 Consulta de busca (opcional)",
                    placeholder="Ex: energia solar, sustentabilidade," +
                    " meio ambiente",
                    help="Termos específicos para busca no banco de dados." +
                    " Se não fornecido, usará o tema como consulta",
                    key="search_input")

                # Organizar campos lado a lado
                col_plat, col_tone = st.columns(2)

                with col_plat:
                    # Seleção de plataforma
                    platform_options = list(PLATFORMS.keys())
                    platform_display = {
                        k: f"{v}" for k,
                        v in PLATFORMS.items()}
                    platform_display["GENERIC"] = (
                        "Genérico (sem plataforma específica)"
                    )
                    platform_options.insert(0, "GENERIC")

                    selected_platform = st.selectbox(
                        "📱 Plataforma de destino",
                        platform_options,
                        format_func=(
                            lambda x: platform_display.get(x, x)
                        ),  # type: ignore
                        help="Selecione a plataforma onde" +
                        " o conteúdo será publicado",
                        key="platform_input"
                    )  # type: ignore

                with col_tone:
                    # Tom da linguagem (otimizado sem duplicações)
                    tone_options = [
                        "informal",
                        "formal",
                        "educativo",
                        "técnico",
                        "inspiracional"
                    ]
                    selected_tone = st.selectbox(
                        "📝 Tom da linguagem",
                        tone_options,
                        index=0,
                        help="Defina o tom que melhor se " +
                        "adequa ao seu público",
                        key="tone_input")

                # Segunda linha de campos emparelhados
                col_length, col_creativity = st.columns(2)

                with col_length:
                    selected_word_count = st.slider(
                        "📏 Quantidade de palavras",
                        min_value=50,
                        max_value=800,
                        value=300,
                        step=25,
                        help="Defina exatamente quantas palavras o post terá",
                        key="word_count_input"
                    )
                    # Converter para formato esperado pelo backend
                    selected_length = f"Exato ({selected_word_count} palavras)"

                with col_creativity:
                    selected_creativity = st.selectbox(
                        "🎨 Nível de criatividade",
                        [
                            "conservador",
                            "equilibrado",
                            "criativo",
                            "inovador"
                        ],
                        index=1,
                        help="Controle o nível de criatividade e " +
                        "originalidade do post",
                        key="creativity_input")

                # Terceira linha de configurações adicionais
                col_hashtags, col_cta = st.columns(2)

                with col_hashtags:
                    include_hashtags = st.checkbox(
                        "#️⃣ Incluir hashtags",
                        value=True,
                        help="Adicionar hashtags relevantes ao conteúdo"
                    )

                with col_cta:
                    include_cta = st.checkbox(
                        "📢 Incluir call-to-action",
                        value=False,
                        help="Adicionar chamada para ação no final do post"
                    )

                # Botão de gerar sempre visível
                st.markdown("<br>", unsafe_allow_html=True)
                generate_button = st.button(
                    "🚀 Gerar Post",
                    use_container_width=True,
                    type="primary",
                    key="generate_btn",
                    help="Clique para gerar o post com os parâmetros")

            # Área de resultado
            with col_result:
                result_container = st.container()

                # Estado inicial
                if not generate_button and 'last_generated' not in (
                    st.session_state
                ):
                    with result_container:
                        st.markdown("""
                        <div style="
                            background: #f8f9fa;
                            padding: 40px;
                            border-radius: 15px;
                            border: 2px dashed #dee2e6;
                            text-align: center;
                            min-height: 400px;
                            display: flex;
                            flex-direction: column;
                            justify-content: center;
                        ">
                            <h3 style="color: #6c757d;">📄 Resultado</h3>
                            <p style="color: #6c757d;">
                                O post gerado aparecerá aqui
                            </p>
                            <div style="margin-top: 20px; font-size: 48px;">
                                🤖
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                # Exibir último resultado se existe
                elif 'last_generated' in st.session_state:
                    last_data = st.session_state['last_generated']
                    with result_container:
                        st.toast("Post anterior carregado", icon="📄")

                        # Mostrar informações do post anterior
                        st.markdown(f"""
                        <div style="
                            background: #e7f3ff;
                            padding: 10px;
                            border-radius: 6px;
                            margin-bottom: 15px;
                            font-size: 12px;
                            border-left: 3px solid #007bff;
                        ">
                            📱 {last_data.get('platform', 'N/A')} • 📝 {
                            last_data.get('tone', 'N/A').title()} •
                            🎨 {last_data.get(
                                'creativity', 'N/A'
                            ).title()} • 📏 {last_data.get('length', 'N/A')}
                        </div>
                        """, unsafe_allow_html=True)

                        # Mostrar texto anterior
                        formatted_text = last_data.get('text', '').replace(
                            '\n', '<br>'
                        )
                        st.markdown(f"""
                        <div style="
                            background: white;
                            padding: 20px;
                            border-radius: 10px;
                            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                            border: 1px solid #e9ecef;
                            margin-bottom: 15px;
                            line-height: 1.6;
                            font-size: 15px;
                        ">
                        {formatted_text}
                        </div>
                        """, unsafe_allow_html=True)

            # Processar geração se botão foi clicado
            if generate_button:
                # Validação com toast apenas quando necessário
                if not text_topic or not text_topic.strip():
                    st.toast(
                        "Por favor, preencha o tema do post!",
                        icon="⚠️")
                elif len(text_topic.strip()) < 5:
                    st.toast(
                        "O tema deve ter pelo menos 5 caracteres!",
                        icon="⚠️")
                elif len(text_topic.strip()) > 500:
                    st.toast(
                        "O tema deve ter no máximo 500 caracteres!",
                        icon="❌")
                else:
                    # Validação passou - processar geração
                    query = search_query if search_query else (
                        text_topic.strip()
                    )
                    platform_code = selected_platform if (
                        selected_platform != "GENERIC"
                    ) else ""

                    self._process_text_generation_improved(
                        text_topic.strip(),
                        query,
                        platform_code,
                        selected_tone,
                        selected_creativity,
                        selected_length,
                        include_hashtags,
                        include_cta,
                        token,
                        result_container
                    )

        elif 'create' not in permissions:
            st.markdown("""
            <div style="text-align: center; padding: 50px;
                background: #fff3cd; border-radius: 15px;
                border: 1px solid #ffeaa7;">
                <h3 style="color: #856404;">🔒 Acesso Restrito</h3>
                <p style="color: #856404; font-size: 1.1rem;">
                    Você não possui permissão para gerar posts.<br>
                    Entre em contato com o administrador do sistema.
                </p>
            </div>
            """, unsafe_allow_html=True)

    def render(self, token, menu_position, permissions):
        """
        Interface para renderização dos posts gerados.

        Parameters
        ----------
        token : str
            O token utilizado no envio da requisição.
        menu_position : Any
            posição do menu superior com a listagem dos posts.
        permissions : list
            Lista contendo as permissões do usuário.
        """
        if 'read' in permissions:

            texts = TextsRequest().get_texts(token)

            if not texts:
                st.markdown("""
                <div style="text-align: center; padding: 50px;
                    background: #f8f9fa; border-radius: 15px;
                    border: 2px dashed #dee2e6;">
                    <h3 style="color: #6c757d;">📄 Nenhum post encontrado</h3>
                    <p style="color: #6c757d; font-size: 1.1rem;">
                        Que tal gerar seu primeiro post usando IA?
                    </p>
                    <p style="color: #6c757d;">
                        Vá para <strong>Gerar post</strong> no menu
                        para começar.
                    </p>
                </div>
                """, unsafe_allow_html=True)
                return

            # Filtros
            st.markdown("### 🔍 Filtros")
            col_filter1, col_filter2 = st.columns(2)

            with col_filter1:
                status_filter = st.selectbox(
                    "Status:",
                    ["Todos", "Aprovado", "Pendente"],
                    index=0
                )

            with col_filter2:
                search_text = st.text_input(
                    "🔎 Buscar por tema:",
                    placeholder="Digite palavras-chave do tema..."
                )

            # Filtrar posts
            filtered_texts = texts
            if status_filter != "Todos":
                if status_filter == "Aprovado":
                    filtered_texts = [
                        t for t in filtered_texts if t.get(
                            'is_approved', False)]
                elif status_filter == "Pendente":
                    filtered_texts = [
                        t for t in filtered_texts if not t.get(
                            'is_approved', False)]

            if search_text:
                filtered_texts = [
                    t for t in filtered_texts if search_text.lower() in t.get(
                        'theme', '').lower()]

            if not filtered_texts:
                st.toast(
                    body="Nenhum post encontrado com os filtros aplicados.",
                    icon="🔍"
                )
                return

            # Exibir posts em cards
            st.markdown("### 📋 Lista de Posts")

            for i, text in enumerate(filtered_texts):
                # Card do post - mapear is_approved da API para status visual
                is_approved = text.get('is_approved', False)
                status_color = '#28a745' if is_approved else '#ffc107'
                status_emoji = '✅' if is_approved else '⏳'
                status_text = 'Aprovado' if is_approved else 'Pendente'

                # Usar 'content' da API como post gerado
                content_text = text.get('content', text.get(
                    'generated_text', 'Post não disponível'))

                # Container principal do card
                with st.container():
                    # Formatação da data brasileira
                    created_date = text.get('created_at', 'N/A')
                    if created_date != 'N/A' and len(created_date) >= 10:
                        try:
                            from datetime import datetime
                            date_obj = datetime.strptime(
                                created_date[:10],
                                '%Y-%m-%d'
                            )
                            br_date = date_obj.strftime('%d/%m/%Y')
                        except Exception:
                            br_date = created_date[:10]
                    else:
                        br_date = 'N/A'

                    theme_display = text.get('theme', 'Sem título')[:100]
                    theme_display += '...' if (
                        len(text.get('theme', '')) > 100
                    ) else ''

                    st.markdown(f"""
                    <div style="
                        background: linear-gradient(145deg, #ffffff, #f8f9fa);
                        padding: 20px;
                        border-radius: 15px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                        border-left: 5px solid {status_color};
                        margin-bottom: 15px;
                        display: flex;
                        gap: 20px;
                    ">
                        <div style="flex: 0 0 300px;">
                            <h4 style="color: #333; margin-bottom: 10px;">
                                {status_emoji} {theme_display}
                            </h4>
                            <p style="color: #666; margin-bottom: 10px;">
                                <strong>Status:</strong> <span style="color: {
                        status_color
                    };">{status_text}</span>
                            </p>
                            <p style="color: #666; margin-bottom: 10px;">
                                <strong>📅 Data:</strong> {br_date}
                            </p>
                            <p style="color: #666; margin-bottom: 15px;">
                                <strong>📱 Plataforma:</strong> {
                        PLATFORMS.get(
                            text.get('platform', 'N/A'), 'N/A')
                    }
                            </p>
                        </div>
                        <div style="
                            flex: 1;
                            background: #f8f9fa;
                            padding: 15px;
                            border-radius: 8px;
                            max-height: 200px;
                            overflow-y: auto;
                            border: 1px solid #dee2e6;
                        ">
                            <h5 style="color: #333; margin-bottom: 10px;">
                                📄 Conteúdo:
                            </h5>
                            <div style="color: #555;
                                font-size: 0.9rem; line-height: 1.4;">
                                {content_text[:500]}{'...' if len(
                        content_text
                    ) > 500 else ''}
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Botões de ação para cada post (sempre visíveis)
                    col_btn1, col_btn2, col_btn3 = st.columns(3)

                    text_id = text.get('id')

                    with col_btn1:
                        # Botão aprovar - ativo se não aprovado e tem permissão
                        approve_key = f"approve_{text_id}_{i}"
                        approve_disabled = is_approved or 'update' not in (
                            permissions
                        )
                        approve_type = "secondary" if not (
                            approve_disabled
                        ) else "primary"

                        if st.button(
                            "✅ Aprovar" if not is_approved else "✅ Aprovado",
                            key=approve_key,
                            use_container_width=True,
                            type=approve_type,
                            disabled=approve_disabled,
                                help="Aprovar post" if not (
                                    approve_disabled
                                ) else "Post já aprovado"):
                            with st.spinner("Aprovando post..."):
                                result = TextsRequest().approve_text(
                                    token,
                                    text_id
                                )
                            st.toast(result, icon="✅")
                            st.rerun()

                    with col_btn2:
                        # Botão reprovar - ativo se aprovado e tem permissão
                        reject_key = f"reject_{text_id}_{i}"
                        reject_disabled = not is_approved or 'update' not in (
                            permissions
                        )
                        reject_type = "secondary" if not reject_disabled else (
                            "primary"
                        )

                        if st.button(
                            "❌ Reprovar" if is_approved else "❌ Reprovado",
                            key=reject_key,
                            use_container_width=True,
                            type=reject_type,
                            disabled=reject_disabled,
                                help="Reprovar post" if not (
                                    reject_disabled
                                ) else "Post já reprovado"):
                            with st.spinner("Reprovando post..."):
                                result = TextsRequest().reject_text(
                                    token,
                                    text_id
                                )
                            st.toast(result, icon="❌")
                            st.rerun()

                    with col_btn3:
                        # Botão regenerar - sempre ativo se tem permissão
                        regenerate_key = f"regenerate_{text_id}_{i}"
                        regenerate_disabled = 'create' not in permissions

                        if st.button(
                            "🔄 Regenerar",
                            key=regenerate_key,
                            use_container_width=True,
                            type="secondary",
                            disabled=regenerate_disabled,
                            help="Regenerar este post" if not (
                                regenerate_disabled
                            ) else "Sem permissão para regenerar"
                        ):
                            # Armazenar dados do post para regeneração
                            st.session_state.regenerate_text_data = {
                                'theme': text.get('theme', ''),
                                'original_id': text_id
                            }
                            st.toast(
                                "Dados salvos!",
                                icon="📝"
                            )

            # Paginação simples
            if len(filtered_texts) > 10:
                st.toast(
                    f"""Exibindo {
                        min(10, len(filtered_texts))
                    } de {len(filtered_texts)} posts""",
                    icon="📄")

        elif 'read' not in permissions:
            st.markdown("""
            <div style="text-align: center; padding: 50px;
                background: #fff3cd; border-radius: 15px;
                border: 1px solid #ffeaa7;">
                <h3 style="color: #856404;">🔒 Acesso Restrito</h3>
                <p style="color: #856404; font-size: 1.1rem;">
                    Você não possui permissão para visualizar posts.<br>
                    Entre em contato com o administrador do sistema.
                </p>
            </div>
            """, unsafe_allow_html=True)

    def update(self, token, menu_position, permissions):
        """
        Menu com interface para atualização do post.

        Parameters
        ----------
        token : str
            O token utilizado no envio da requisição.
        menu_position : Any
            posição do menu superior com a listagem dos posts.
        permissions : list
            Lista contendo as permissões do usuário.
        """

        if 'update' in permissions:
            texts = TextsRequest().get_texts(token)

            if not texts:
                st.markdown("""
                <div style="text-align: center; padding: 50px;
                    background: #f8f9fa; border-radius: 15px;
                    border: 2px dashed #dee2e6;">
                    <h3 style="color: #6c757d;">📄 Nenhum post encontrado</h3>
                    <p style="color: #6c757d; font-size: 1.1rem;">
                        Não há posts disponíveis para edição.
                    </p>
                </div>
                """, unsafe_allow_html=True)
                return

            # Seleção do post no menu superior
            with menu_position:
                st.markdown("### 🎯 Selecionar Post")
                texts_options = {}
                for text in texts:
                    theme_preview = text['theme'][:50]
                    theme_preview += '...' if len(text['theme']) > 50 else ''
                    status = ('Aprovado' if text.get('is_approved')
                              else 'Pendente')
                    key = f"{theme_preview} ({status})"
                    texts_options[key] = text['id']

                selected_text_display = st.selectbox(
                    "Escolha o post para editar:",
                    options=list(texts_options.keys()),
                    help="Selecione um post da lista para editar"
                )
                selected_text_id = texts_options[selected_text_display]

            # Interface de edição
            text_data = TextsRequest().get_text(token, selected_text_id)

            if text_data:
                col_form, col_preview = st.columns([1, 1])

                with col_form:
                    st.subheader("📝 Dados do Post")

                    new_topic = st.text_area(
                        label="🎯 Tema",
                        value=text_data['theme'],
                        max_chars=500,
                        help="Atualize o tema do post",
                        height=100
                    )

                    status_options = {
                        True: "✅ Aprovado",
                        False: "⏳ Pendente"
                    }

                    current_approval_status = text_data.get(
                        'is_approved', False)

                    new_status_display = st.selectbox(
                        label="📊 Status",
                        options=list(status_options.values()),
                        index=0 if current_approval_status else 1,
                        help="Atualize o status do post"
                    )

                    # Converter de volta para o valor da API
                    new_approval_status = new_status_display == "✅ Aprovado"

                    st.markdown("</div>", unsafe_allow_html=True)

                with col_preview:

                    st.subheader("👁️ Prévia das Alterações")

                    # Verificar se houve mudanças
                    has_changes = (
                        new_topic != text_data['theme'] or
                        new_approval_status != text_data.get('is_approved'))

                    if has_changes:
                        st.toast("Alterações detectadas!", icon="📝")
                    else:
                        st.toast("Nenhuma alteração feita", icon="ℹ️")

                    if new_topic:
                        topic_preview = (new_topic[:200]
                                         if len(new_topic) > 200
                                         else new_topic)
                        topic_suffix = '...' if len(new_topic) > 200 else ''
                        st.markdown(f"""
                        **🎯 Novo Tema:**
                        {topic_preview}{topic_suffix}

                        **📊 Novo Status:**
                        {status_options[new_approval_status]}

                        **📅 Data Original:**
                        {text_data.get('created_at', 'N/A')[:10]}

                        **📊 Caracteres:** {len(new_topic)}/500""")

                    st.markdown("</div>", unsafe_allow_html=True)

                # Validação e botão de atualização
                if new_topic:
                    validated_topic, topic_data = self.validate_topic(
                        new_topic)

                    if validated_topic and has_changes:
                        st.markdown("<br>", unsafe_allow_html=True)
                        col_btn1, col_btn2, col_btn3 = st.columns([1, 2, 1])

                        with col_btn2:
                            confirm_button = st.button(
                                "💾 Salvar Alterações",
                                use_container_width=True,
                                type="primary",
                                help="Confirmar as alterações no post"
                            )

                            if confirm_button:
                                new_text_data = {
                                    "theme": topic_data,
                                    "is_approved": new_approval_status
                                }

                                with st.spinner("Salvando alterações..."):
                                    returned_text = TextsRequest().update_text(
                                        token=token,
                                        text_id=text_data['id'],
                                        updated_data=new_text_data
                                    )

                                st.toast(
                                    "Post atualizado com sucesso!",
                                    icon="✅"
                                )
                                st.balloons()
                                st.toast(returned_text, icon="ℹ️")
                                st.rerun()

                # Área de prévia do post completo
                with st.expander(
                    "📄 Visualizar Post Completo",
                    expanded=False
                ):
                    content_text = text_data.get(
                        'content', text_data.get(
                            'generated_text', 'Post não disponível'))
                    st.markdown(f"""
                    <div style="
                        background: #f8f9fa;
                        padding: 20px;
                        border-radius: 10px;
                        max-height: 400px;
                        overflow-y: auto;
                        border: 1px solid #dee2e6;
                    ">
                        {content_text}
                    </div>
                    """, unsafe_allow_html=True)

        elif 'update' not in permissions:
            st.markdown("""
            <div style="text-align: center; padding: 50px;
                background: #fff3cd; border-radius: 15px;
                border: 1px solid #ffeaa7;">
                <h3 style="color: #856404;">🔒 Acesso Restrito</h3>
                <p style="color: #856404; font-size: 1.1rem;">
                    Você não possui permissão para atualizar posts.<br>
                    Entre em contato com o administrador do sistema.
                </p>
            </div>
            """, unsafe_allow_html=True)

    def main_menu(self, token, permissions):
        """
        Menu principal da aplicação de geração de posts.

        Parameters
        ----------
        token : str
            Token utilizado para as requisições.
        permissions : str
            Lista com as permissões do usuário.
        """
        class_permissions = TextsRequest().get_text_permissions(
            user_permissions=permissions
        )

        # Cabeçalho principal mais limpo
        col_header, col_menu, col_actions = st.columns([1, 1.2, 1])

        with col_menu:
            # Menu com ícones mais intuitivos
            menu_options = {
                "📚 Biblioteca de Posts": self.render,
                "🚀 Gerar Novo Post": self.create,
            }

            # Verificar permissões e filtrar opções disponíveis
            available_options = {}
            if 'read' in class_permissions:
                available_options["📚 Biblioteca de Posts"] = (
                    menu_options["📚 Biblioteca de Posts"]
                )
            if 'create' in class_permissions:
                available_options["🚀 Gerar Novo Post"] = (
                    menu_options["🚀 Gerar Novo Post"]
                )

            if available_options:
                selected_option = st.selectbox(
                    label="Escolha uma ação:",
                    options=list(available_options.keys()),
                    help="Selecione a operação que deseja realizar",
                    label_visibility="collapsed"
                )
            else:
                st.toast(
                    "Você não possui permissões para usar esta funcionalidade",
                    icon="❌")
                return

        # Mostrar informações de permissão
        with col_actions:
            permission_status = []
            if 'read' in class_permissions:
                permission_status.append("👁️ Visualizar")
            if 'create' in class_permissions:
                permission_status.append("➕ Criar")

        st.divider()

        # Executar a opção selecionada
        if available_options:
            executed_option = available_options[selected_option]
            executed_option(
                token=token,
                menu_position=col_actions,
                permissions=class_permissions
            )

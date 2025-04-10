from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import JSONResponse
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, PictureDescriptionApiOptions, PictureDescriptionVlmOptions
from docling.document_converter import PdfFormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling_core.types.doc import PictureItem, ImageRefMode
import uuid
import os
import tempfile
from typing import Optional, Dict, List, Any, Literal
import logging
import shutil
import time
import inspect
import base64
import re
import traceback

# Importação para o OCR aprimorado
from app.enhanced_ocr import apply_enhanced_ocr, process_document_with_ocr

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.post("/process")
async def process_file(
    file: UploadFile = File(...),
    ocr: bool = Query(False, description="Ativa OCR para leitura de imagens"),
    enhanced_ocr: bool = Query(
        False, description="Ativa OCR aprimorado que processa cada imagem individualmente"),
    export_format: str = Query(
        "markdown", description="Formato de exportação: markdown, html ou dict"),
    generate_images: bool = Query(
        False, description="Gera imagens em base64 para as páginas e figuras"),
    ocr_language: str = Query(
        "pt", description="Idioma para OCR (ex: pt, en, fr, de)"),
    ocr_min_confidence: float = Query(
        0.5, description="Confiança mínima para OCR (entre 0 e 1)"),
    describe_images: bool = Query(
        False, description="Adiciona descrições às imagens usando LLM"),
    image_description_prompt: str = Query(
        "Descreva esta imagem em detalhes", description="Prompt para descrição de imagens"),
    include_image_data: bool = Query(
        False, description="Inclui imagens como base64 no markdown (se false, apenas placeholders)"),
    include_descriptions: bool = Query(
        True, description="Inclui descrições de imagens no markdown quando disponíveis"),
    # Parâmetros para escolha do provedor de LLM
    llm_provider: str = Query(
        "ollama", description="Provedor LLM a ser usado: 'ollama', 'openai' ou 'interno'"),
    # Parâmetros para Ollama
    ollama_url: str = Query("http://localhost:11434/v1/chat/completions",
                            description="URL da API Ollama para descrição de imagens"),
    ollama_model: str = Query(
        "llama3.2-vision:latest", description="Modelo Ollama a ser usado para descrição de imagens"),
    # Parâmetros para OpenAI
    openai_api_key: Optional[str] = Query(
        None, description="Chave da API OpenAI para descrição de imagens"),
    openai_model: str = Query(
        "gpt-4o-mini", description="Modelo OpenAI a ser usado para descrição de imagens"),
    openai_max_tokens: int = Query(
        500, description="Número máximo de tokens para resposta da OpenAI")
):
    temp_dir = None
    file_path = None

    try:
        export_format = export_format.strip().lower()
        logger.info(f"Formato de exportação após limpeza: '{export_format}'")

        # Ler conteúdo do arquivo
        file_content = await file.read()

        # Criar diretório temporário
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file.filename)

        # Salvar arquivo
        with open(file_path, "wb") as f:
            f.write(file_content)

        logger.info(f"Arquivo '{file.filename}' salvo para processamento")

        # Verificar se devemos usar OCR aprimorado ou OCR padrão
        use_enhanced_ocr = enhanced_ocr
        use_standard_ocr = ocr and not enhanced_ocr

        # Configurar opções de pipeline para PDF
        pipeline_options = PdfPipelineOptions()

        # Configurar OCR padrão (método original) se solicitado
        if use_standard_ocr:
            logger.info(f"Ativando OCR padrão com idioma: {ocr_language}")
            # Definir idioma do OCR
            pipeline_options.do_ocr = True

            # Verificar e definir configurações de OCR
            try:
                # Abordagem 1: definir via ocr_options.lang
                if hasattr(pipeline_options, "ocr_options") and hasattr(pipeline_options.ocr_options, "lang"):
                    pipeline_options.ocr_options.lang = [ocr_language]

                # Abordagem 2: definir via ocr_languages (algumas versões)
                if hasattr(pipeline_options, "ocr_languages"):
                    pipeline_options.ocr_languages = [ocr_language]

                # Forçar uso do EasyOCR se disponível
                if hasattr(pipeline_options.ocr_options, "use_easyocr"):
                    pipeline_options.ocr_options.use_easyocr = True

                # Ajustar confiança mínima
                if hasattr(pipeline_options.ocr_options, "min_confidence"):
                    pipeline_options.ocr_options.min_confidence = ocr_min_confidence
            except AttributeError as e:
                logger.warning(
                    f"Erro ao configurar opções específicas de OCR: {e}")
        else:
            # Desativar OCR padrão
            pipeline_options.do_ocr = False

        # Configuração para geração de imagens
        # Sempre geramos imagens se OCR aprimorado estiver ativado
        if generate_images or use_enhanced_ocr:
            logger.info("Ativando geração de imagens")

            # Configurações básicas
            pipeline_options.generate_page_images = True
            pipeline_options.generate_picture_images = True
            pipeline_options.generate_table_images = True

            # Configurações para melhorar a qualidade das imagens
            pipeline_options.images_scale = 1.5  # Escala maior para melhor qualidade
            pipeline_options.generate_parsed_pages = True

            # Configurações para garantir que as imagens sejam incorporadas
            try:
                if hasattr(pipeline_options, "with_embedded_pictures"):
                    pipeline_options.with_embedded_pictures = True

                if hasattr(pipeline_options, "embed_images"):
                    pipeline_options.embed_images = True
            except AttributeError as e:
                logger.warning(
                    f"Erro ao configurar incorporação de imagens: {e}")

        # Configurar descrição de imagens usando LLM se solicitado
        if describe_images:
            logger.info(
                f"Configurando descrição de imagens com provedor: {llm_provider}")

            # Ativar a descrição de imagens
            pipeline_options.do_picture_description = True

            # Escolher o provedor de LLM para descrição de imagens
            if llm_provider.lower() == "ollama":
                # Configurar usando API Ollama
                pipeline_options.picture_description_options = PictureDescriptionApiOptions(
                    url=ollama_url,
                    params={
                        "model": ollama_model,
                        "max_completion_tokens": 500
                    },
                    prompt=image_description_prompt,
                    timeout=90
                )
                pipeline_options.enable_remote_services = True
                logger.info(f"Usando API Ollama para descrição de imagens")

            elif llm_provider.lower() == "openai":
                # Verificar se a chave da API foi fornecida
                if not openai_api_key:
                    raise ValueError(
                        "API key da OpenAI é necessária quando usando o provedor 'openai'")

                # Configurar usando API OpenAI
                pipeline_options.picture_description_options = PictureDescriptionApiOptions(
                    url="https://api.openai.com/v1/chat/completions",
                    params={
                        "model": openai_model,
                        "max_tokens": openai_max_tokens
                    },
                    prompt=image_description_prompt,
                    timeout=120,
                    headers={
                        "Authorization": f"Bearer {openai_api_key}",
                        "Content-Type": "application/json"
                    }
                )
                pipeline_options.enable_remote_services = True
                logger.info(f"Usando API OpenAI para descrição de imagens")

            else:
                # Usar o modelo interno do Docling (fallback)
                pipeline_options.picture_description_options = PictureDescriptionVlmOptions(
                    prompt=image_description_prompt,
                    generation_config={
                        "max_new_tokens": 200, "do_sample": False}
                )
                logger.info(
                    "Usando modelo VLM interno do Docling para descrição de imagens")

        # Criar conversor com as opções configuradas
        doc_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=pipeline_options,
                    backend=PyPdfiumDocumentBackend
                )
            }
        )

        # Converter o documento
        logger.info(f"Iniciando conversão do arquivo")
        start_time = time.time()
        conv_result = doc_converter.convert(file_path)
        end_time = time.time() - start_time
        logger.info(f"Documento convertido em {end_time:.2f} segundos")

        # Verificar se o método iterate_items existe
        has_iterate_items = hasattr(conv_result.document, 'iterate_items')

        # IMPORTANTE: Verificar o formato solicitado e aplicar OCR aprimorado se necessário
        # Verificar se devemos aplicar OCR aprimorado após conversão
        if use_enhanced_ocr:
            logger.info(f"Aplicando OCR aprimorado com idioma: {ocr_language}")
            try:
                # Para formato markdown, aplicamos OCR e retornamos o resultado como markdown
                if export_format.lower() == "markdown":
                    md_content = apply_enhanced_ocr(
                        conv_result,
                        temp_dir,
                        ocr_language=ocr_language,
                        min_confidence=ocr_min_confidence,
                        include_image_data=include_image_data
                    )
                    logger.info(
                        "OCR aprimorado aplicado com sucesso para formato markdown")
                    return {"markdown": md_content}
                else:
                    # Para outros formatos, processamos o documento com OCR mas não retornamos ainda
                    process_document_with_ocr(
                        conv_result,
                        temp_dir,
                        ocr_language=ocr_language,
                        min_confidence=ocr_min_confidence,
                        include_image_data=True
                    )
                    logger.info(
                        f"OCR aprimorado aplicado, continuando para exportação no formato: {export_format}")
                    # Não retornamos aqui - continuamos o fluxo para exportar no formato solicitado
            except Exception as e:
                logger.error(f"Erro ao aplicar OCR aprimorado: {e}")
                logger.error(traceback.format_exc())
                logger.warning(
                    "Continuando com o fluxo normal de processamento após falha no OCR aprimorado")

        # Exportar no formato solicitado conforme a requisição
        # Importante: para markdown só continuamos se não houver OCR aprimorado
        if export_format.lower() == "markdown" and not use_enhanced_ocr:
            md_content = None

            # Abordagem com placeholders únicos para cada imagem
            try:
                # Primeiro, vamos coletar as descrições das imagens
                all_descriptions = []

                if has_iterate_items:
                    for element, *_ in conv_result.document.iterate_items():
                        if isinstance(element, PictureItem):
                            if hasattr(element, 'annotations') and element.annotations:
                                desc = "\n".join(
                                    [ann.text for ann in element.annotations])
                                all_descriptions.append(desc)
                            else:
                                # Verificar outros atributos para descrição
                                for attr in ['description', 'caption', 'text', 'ocr_result']:
                                    if hasattr(element, attr) and getattr(element, attr):
                                        all_descriptions.append(
                                            getattr(element, attr))
                                        break
                                else:
                                    all_descriptions.append(
                                        "Sem descrição disponível")

                # Número total de descrições coletadas
                num_descriptions = len(all_descriptions)
                logger.info(
                    f"Coletadas {num_descriptions} descrições de imagens")

                # Criar placeholders únicos para cada imagem
                unique_placeholders = []
                for i in range(num_descriptions):
                    unique_placeholders.append(f"<!-- image-{i+1} -->")

                # Criar um arquivo temporário para o markdown
                md_file_with_unique_placeholders = os.path.join(
                    temp_dir, f"with_unique_placeholders.md")

                # Definir o modo de imagem
                image_mode_value = "embedded" if include_image_data else "placeholder"

                # Primeiro, salvar o markdown normalmente
                temp_md_file = os.path.join(temp_dir, "temp_md.md")
                conv_result.document.save_as_markdown(
                    temp_md_file,
                    image_mode=image_mode_value
                )

                # Ler o conteúdo do arquivo
                with open(temp_md_file, 'r', encoding='utf-8') as f:
                    md_content = f.read()

                # Substituir os placeholders padrão por placeholders únicos
                if not include_image_data:
                    # Padrão de placeholder para substituição
                    placeholder_pattern = "<!-- image -->"

                    # Contar quantos placeholders existem no documento
                    placeholder_count = md_content.count(placeholder_pattern)
                    logger.info(
                        f"Encontrados {placeholder_count} placeholders no markdown")

                    # Substituir cada placeholder padrão por um placeholder único
                    for i in range(min(placeholder_count, num_descriptions)):
                        md_content = md_content.replace(
                            placeholder_pattern, unique_placeholders[i], 1)
                else:
                    # Para imagens em base64, precisamos localizar cada ocorrência
                    img_pattern = r'!\[.*?\]\(data:image\/[^)]+\)'
                    img_matches = list(re.finditer(img_pattern, md_content))

                    # Substituir cada imagem pelo formato:
                    # imagem + placeholder único
                    for i, match in enumerate(img_matches):
                        if i < num_descriptions:
                            img_tag = match.group(0)
                            replacement = f"{img_tag}\n\n{unique_placeholders[i]}"
                            md_content = md_content.replace(
                                img_tag, replacement, 1)

                # Agora, adicionar as descrições nos placeholders únicos
                if include_descriptions and all_descriptions:
                    for i, placeholder in enumerate(unique_placeholders):
                        if i < len(all_descriptions):
                            desc_text = f"\n\n**Descrição da imagem:** {all_descriptions[i]}\n\n"
                            md_content = md_content.replace(
                                placeholder, desc_text)

                # Escrever o markdown modificado em um arquivo
                with open(md_file_with_unique_placeholders, 'w', encoding='utf-8') as f:
                    f.write(md_content)

                logger.info(f"Markdown gerado com placeholders únicos")

            except Exception as e:
                logger.warning(
                    f"Erro ao processar markdown com placeholders únicos: {e}")
                # Fallback para o método padrão
                try:
                    # Definir o modo de imagem
                    image_mode_value = "embedded" if include_image_data else "placeholder"
                    logger.info(
                        f"Usando image_mode='{image_mode_value}' para markdown (fallback)")

                    # Criar arquivo temporário para o markdown
                    md_file = os.path.join(temp_dir, f"{uuid.uuid4()}.md")

                    # Salvar markdown com o modo apropriado
                    conv_result.document.save_as_markdown(
                        md_file,
                        image_mode=image_mode_value
                    )

                    # Ler o arquivo salvo
                    with open(md_file, 'r', encoding='utf-8') as f:
                        md_content = f.read()

                except Exception as inner_e:
                    logger.warning(
                        f"Erro ao criar markdown (fallback): {inner_e}")
                    md_content = conv_result.document.export_to_markdown()

            # Se ainda não temos conteúdo markdown, usar export_to_markdown como último recurso
            if md_content is None:
                logger.warning("Usando export_to_markdown como último recurso")
                md_content = conv_result.document.export_to_markdown()

            return {"markdown": md_content}

        elif export_format.lower() == "html":
            # Para HTML, tentar exportação com parâmetros para incluir imagens
            try:
                # Definir opções para exportação HTML com imagens
                html_options = {}

                # Se a API do documento suporta exportação HTML com imagens embutidas
                if hasattr(conv_result.document, "export_to_html_with_options") or hasattr(conv_result.document, "save_as_html"):
                    # Tentar com parâmetros que forçam a inclusão de imagens
                    if hasattr(conv_result.document, "export_to_html_with_options"):
                        html_content = conv_result.document.export_to_html_with_options(
                            embedding_images=True,
                            image_mode="embedded"
                        )
                    # Alternativa: usar save_as_html se disponível
                    elif hasattr(conv_result.document, "save_as_html"):
                        # Caminho temporário para o HTML
                        html_file = os.path.join(
                            temp_dir, f"{uuid.uuid4()}.html")
                        conv_result.document.save_as_html(
                            html_file,
                            image_mode="embedded"
                        )
                        # Ler arquivo HTML salvo
                        with open(html_file, 'r', encoding='utf-8') as f:
                            html_content = f.read()
                    else:
                        # Fallback para o método padrão
                        html_content = conv_result.document.export_to_html()
                else:
                    # Método padrão de exportação
                    html_content = conv_result.document.export_to_html()

                # Se quisermos adicionar descrições ao HTML
                if describe_images and include_descriptions and has_iterate_items:
                    # Coletar descrições
                    descriptions = []
                    for element, *_ in conv_result.document.iterate_items():
                        if isinstance(element, PictureItem) and hasattr(element, 'annotations') and element.annotations:
                            description = "\n".join(
                                [ann.text for ann in element.annotations])
                            descriptions.append(description)

                    # Adicionar descrições ao HTML (se houver)
                    if descriptions:
                        # Identificar tags de imagem
                        img_tags = re.finditer(r'<img[^>]+>', html_content)

                        new_html = html_content
                        offset = 0

                        for i, match in enumerate(img_tags):
                            if i < len(descriptions):
                                pos = match.end() + offset
                                desc_html = f'<figcaption><strong>Descrição da imagem:</strong> {descriptions[i]}</figcaption>'
                                # Inserir a descrição após a imagem
                                new_html = new_html[:pos] + \
                                    desc_html + new_html[pos:]
                                offset += len(desc_html)

                        html_content = new_html

                # Adicionar CSS para garantir que legendas apareçam abaixo das imagens
                if "<img" in html_content:
                    # Corrigir a ordem das tags - mover figcaption para depois da img
                    logger.info("Verificando e corrigindo a ordem das tags figure/figcaption/img")
                    
                    # Procurar por figcaption antes da tag img dentro de figure
                    pattern = r'<figure[^>]*>[\s\n]*<figcaption[^>]*>(.*?)</figcaption>[\s\n]*(<img[^>]+>)[\s\n]*</figure>'
                    if re.search(pattern, html_content, re.DOTALL):
                        logger.info("Encontrado figcaption antes de img - corrigindo ordem")
                        # Reordenar: figure -> img -> figcaption
                        html_content = re.sub(pattern, r'<figure>\2<figcaption>\1</figcaption></figure>', 
                                             html_content, flags=re.DOTALL)
                    
                    # Procurar por outro padrão comum sem espaços adicionais
                    pattern2 = r'<figure><figcaption>(.*?)</figcaption>(<img[^>]+>)</figure>'
                    if re.search(pattern2, html_content):
                        logger.info("Encontrado segundo padrão de figcaption antes de img - corrigindo")
                        html_content = re.sub(pattern2, r'<figure>\2<figcaption>\1</figcaption></figure>', html_content)
                    
                    # Verificar especificamente imagens base64
                    pattern3 = r'<figure><figcaption>(.*?)</figcaption>(<img src="data:image/[^"]+")([^>]*>)</figure>'
                    if re.search(pattern3, html_content):
                        logger.info("Encontrado figcaption antes de img base64 - corrigindo")
                        html_content = re.sub(pattern3, r'<figure>\2\3<figcaption>\1</figcaption></figure>', html_content)
                    
                    # Adicionar CSS ao HEAD do documento para posicionamento correto das legendas
                    css_style = '''
                    <style>
                    figure {
                    display: flex;
                    flex-direction: column;
                    }
                    figcaption {
                    margin-top: 8px;
                    margin-bottom: 16px;
                    }
                    /* Estilo adicional para garantir que figcaptions fiquem abaixo das imagens */
                    img + figcaption {
                    display: block;
                    margin-top: 8px;
                    }
                    </style>
                    '''

                    # Adicionar o CSS ao HEAD
                    if "<head>" in html_content:
                        html_content = html_content.replace(
                            "<head>", f"<head>{css_style}")
                    elif "</title>" in html_content:
                        # Adicionar após o título, se existir
                        html_content = html_content.replace("</title>", f"</title>{css_style}")
                    else:
                        # Se não houver tag HEAD, adicionar no início do documento
                        html_content = f"{css_style}{html_content}"

                    logger.info(
                        "CSS para posicionamento de legendas adicionado ao HTML")

            except Exception as e:
                logger.warning(f"Erro ao exportar HTML personalizado: {e}")
                # Adicione isso para ver o erro completo
                logger.warning(traceback.format_exc())
                # Fallback: exportar sem parâmetros
                html_content = conv_result.document.export_to_html()

            # Verificar se o HTML gerado contém imagens
            has_images = "<img" in html_content
            logger.info(
                f"HTML gerado com {len(html_content)} caracteres. Contém imagens: {has_images}")

            # Verificar se o conteúdo HTML tem figcaption para depuração
            has_figcaption = "<figcaption" in html_content
            logger.info(f"HTML contém figcaption: {has_figcaption}")

            # Importante: sempre retornar o conteúdo HTML
            return {"html": html_content}

        elif export_format.lower() in ["dict", "json"]:
            # Para dict/json, usar o método padrão
            try:
                dict_content = conv_result.document.export_to_dict()
            except Exception as e:
                logger.warning(f"Erro ao exportar para dict: {e}")
                # Fallback: se falhar, tentar criar um dict básico
                dict_content = {
                    "error": "Não foi possível exportar para dict/json"}

            # Adicionar informações adicionais para debug
            if isinstance(dict_content, dict):
                dict_content["_debug"] = {
                    "processingTime": f"{end_time:.2f} segundos"
                }

                # Se estamos descrevendo imagens, adicione informações sobre descrições
                if describe_images and has_iterate_items:
                    image_desc_count = 0
                    for element, *_ in conv_result.document.iterate_items():
                        if isinstance(element, PictureItem) and hasattr(element, 'annotations') and element.annotations:
                            image_desc_count += 1

                    dict_content["_debug"]["imagesWithDescriptions"] = image_desc_count
                    dict_content["_debug"]["descriptionProvider"] = llm_provider

                # Adicionar informações sobre OCR aprimorado
                if use_enhanced_ocr:
                    dict_content["_debug"]["enhancedOCR"] = {
                        "enabled": True,
                        "language": ocr_language,
                        "minConfidence": ocr_min_confidence
                    }

            return dict_content

        else:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"Formato de exportação '{export_format}' não suportado."}
            )

    except Exception as e:
        logger.error(f"Erro ao processar documento: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": f"Erro ao processar o arquivo: {str(e)}"}
        )

    finally:
        # Limpar arquivos temporários de forma segura
        try:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Arquivos temporários removidos")
        except Exception as e:
            logger.warning(f"Erro ao limpar diretório temporário: {str(e)}")

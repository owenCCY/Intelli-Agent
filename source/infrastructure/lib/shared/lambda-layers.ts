/**********************************************************************************************************************
 *  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.                                                *
 *                                                                                                                    *
 *  Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance    *
 *  with the License. A copy of the License is located at                                                             *
 *                                                                                                                    *
 *      http://www.apache.org/licenses/LICENSE-2.0                                                                    *
 *                                                                                                                    *
 *  or in the 'license' file accompanying this file. This file is distributed on an 'AS IS' BASIS, WITHOUT WARRANTIES *
 *  OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions    *
 *  and limitations under the License.                                                                                *
 *********************************************************************************************************************/

import {Code, LayerVersion, Runtime} from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import {Construct} from "constructs";
import {BuildConfig} from "./build-config";
import * as pyLambda from "@aws-cdk/aws-lambda-python-alpha";
import {Constants} from "./constants";

export class LambdaLayers {
  constructor(private scope: Construct) { }

  createAPIDefaultLayer() {
      return new LayerVersion(
        this.scope,
        "APIDefaultLambdaLayer",
        {
            code: Code.fromAsset(
                path.join(__dirname, "../../../lambda/layer/api"),
                {
                    bundling: {
                        image: Runtime.PYTHON_3_12.bundlingImage,
                        command: [
                            "bash",
                            "-c",
                            `pip install -r requirements.txt ${BuildConfig.LAYER_PIP_OPTION} -t /asset-output/python`,
                        ],
                    },
                },
            ),
            compatibleRuntimes: [Runtime.PYTHON_3_12],
            description: `${Constants.SOLUTION_NAME} - Default API layer`,
        },
    );
  }


  createEmbeddingLayer() {
    const LambdaEmbeddingLayer = new LayerVersion(
      this.scope,
      "APILambdaEmbeddingLayer",
      {
        code: Code.fromAsset(
          path.join(__dirname, "../../../lambda/embedding"),
          {
            bundling: {
              image: Runtime.PYTHON_3_12.bundlingImage,
              command: [
                "bash",
                "-c",
                `pip install -r requirements.txt ${BuildConfig.LAYER_PIP_OPTION} -t /asset-output/python`,
              ],
            },
          },
        ),
        compatibleRuntimes: [Runtime.PYTHON_3_12],
        description: `LLM Bot - API layer`,
      },
    );
    return LambdaEmbeddingLayer;
  }

  createAgentFlowLayer() {
      return new LayerVersion(
        this.scope,
        "AgentFlowLayer",
        {
            code: Code.fromAsset(
                path.join(__dirname, "../../../lambda/layer/agent-flow"),
                {
                    bundling: {
                        image: Runtime.PYTHON_3_12.bundlingImage,
                        command: [
                            "bash",
                            "-c",
                            `pip install -r requirements.txt ${BuildConfig.LAYER_PIP_OPTION} -t /asset-output/python`,
                        ],
                    },
                },
            ),
            compatibleRuntimes: [Runtime.PYTHON_3_12],
            description: `${Constants.SOLUTION_NAME} - Agent Flow layer`,
        },
    );
  }


  createOnlineSourceLayer() {
    const LambdaOnlineSourceLayer = new pyLambda.PythonLayerVersion(
      this.scope,
      "APILambdaOnlineSourceLayer",
      {
        entry: path.join(__dirname, "../../../lambda/online"),
        compatibleRuntimes: [Runtime.PYTHON_3_12],
        description: `Intelli agent - Online Source layer`,
        bundling: {
          assetExcludes: ["*.pyc","*/__pycache__/*","*.xls","*.xlsx","*.csv","*.png","lambda_main/retail/size/*"],
        }
      },
    );
    return LambdaOnlineSourceLayer;
  }

  createJobSourceLayer() {
    const LambdaJobSourceLayer = new pyLambda.PythonLayerVersion(
      this.scope,
      "APILambdaJobSourceLayer",
      {
        entry: path.join(__dirname, "../../../lambda/job/dep/llm_bot_dep"),
        compatibleRuntimes: [Runtime.PYTHON_3_12],
        description: `Intelli agent - Job Source layer`,
      },
    );
    return LambdaJobSourceLayer;
  }

  createAuthorizerLayer() {
    const LambdaAuthorizerLayer = new pyLambda.PythonLayerVersion(
      this.scope,
      "APILambdaAuthorizerLayer",
      {
        entry: path.join(__dirname, "../../../lambda/authorizer"),
        compatibleRuntimes: [Runtime.PYTHON_3_12],
        description: `Intelli agent - Authorizer layer`,
      },
    );
    return LambdaAuthorizerLayer;
  }
}
